mod support;

use std::sync::{Arc, Mutex};

use ctrlcctrlv_core::catalog::Artifact;
use ctrlcctrlv_core::download::Downloader;
use ctrlcctrlv_core::http::Http;
use sha2::{Digest, Sha256};
use support::{Reply, Server};
use tokio_util::sync::CancellationToken;

fn artifact(url: &str, content: &[u8]) -> Artifact {
    Artifact {
        filename: "model.gguf".into(),
        url: format!("{url}/model"),
        sha256: hex::encode(Sha256::digest(content)),
        size: content.len() as u64,
    }
}

fn downloader() -> Downloader {
    let mut downloader = Downloader::new(Http::new());
    downloader.backoff = 0.0;
    downloader
}

fn quiet(_: &str, _: u64, _: u64) {}

fn part(destination: &std::path::Path) -> std::path::PathBuf {
    destination.with_file_name("model.gguf.part")
}

#[tokio::test]
async fn resume_validates_range_and_hash() {
    let contents = b"0123456789";
    let server = Server::start(|request| {
        assert_eq!(request.header("range"), Some("bytes=4-"));
        assert_eq!(request.header("accept-encoding"), Some("identity"));
        Reply::bytes(206, b"456789", &[("Content-Range", "bytes 4-9/10")])
    })
    .await;
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("model.gguf");
    std::fs::write(part(&destination), &contents[..4]).unwrap();
    downloader()
        .download(&artifact(&server.url, contents), &destination, &CancellationToken::new(), &quiet)
        .await
        .unwrap();
    assert_eq!(std::fs::read(&destination).unwrap(), contents);
    assert!(!part(&destination).exists());
}

#[tokio::test]
async fn server_ignoring_range_restarts_download() {
    let server = Server::start(|_| Reply::bytes(200, b"0123456789", &[])).await;
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("model.gguf");
    std::fs::write(part(&destination), b"0123").unwrap();
    downloader()
        .download(&artifact(&server.url, b"0123456789"), &destination, &CancellationToken::new(), &quiet)
        .await
        .unwrap();
    assert_eq!(std::fs::read(&destination).unwrap(), b"0123456789");
}

#[tokio::test]
async fn corrupt_file_never_becomes_installed() {
    let server = Server::start(|_| Reply::bytes(200, b"corrupt", &[])).await;
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("model.gguf");
    let error = downloader()
        .download(&artifact(&server.url, b"correct"), &destination, &CancellationToken::new(), &quiet)
        .await
        .unwrap_err();
    assert!(error.to_string().contains("Контрольная сумма"));
    assert!(!destination.exists() && !part(&destination).exists());
}

#[tokio::test]
async fn verified_existing_file_is_kept_without_a_request() {
    let server = Server::start(|_| Reply::status(500)).await;
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("model.gguf");
    std::fs::write(&destination, b"data").unwrap();
    downloader()
        .download(&artifact(&server.url, b"data"), &destination, &CancellationToken::new(), &quiet)
        .await
        .unwrap();
    assert!(server.requests().is_empty());
}

#[tokio::test]
async fn cancelled_download_contacts_no_server() {
    let server = Server::start(|_| Reply::status(500)).await;
    let directory = tempfile::tempdir().unwrap();
    let cancel = CancellationToken::new();
    cancel.cancel();
    let error = downloader()
        .download(&artifact(&server.url, b"data"), &directory.path().join("model.gguf"), &cancel, &quiet)
        .await
        .unwrap_err();
    assert!(error.is_cancelled());
    assert!(server.requests().is_empty());
}

#[tokio::test]
async fn wrong_range_rejected() {
    for header in ["bytes 0-3/6", "bytes 2-3/7", "bytes 2-4/4", "bytes 2-3/*", "bytes 2-3/4 extra"] {
        let server = Server::start(move |_| Reply::bytes(206, b"2345", &[("Content-Range", header)])).await;
        let directory = tempfile::tempdir().unwrap();
        let destination = directory.path().join("model.gguf");
        std::fs::write(part(&destination), b"01").unwrap();
        let content: &[u8] = if header.ends_with("/6") { b"012345" } else { b"data" };
        let error = downloader()
            .download(&artifact(&server.url, content), &destination, &CancellationToken::new(), &quiet)
            .await
            .unwrap_err();
        assert!(error.to_string().contains("диапазон"), "{header}: {error}");
        assert!(!destination.exists());
    }
}

#[tokio::test]
async fn network_disconnect_retries_from_bytes_actually_written() {
    let contents: Vec<u8> = [vec![b'a'; 256 * 1024], b"remaining".to_vec()].concat();
    let total = contents.len();
    let calls = Arc::new(Mutex::new(Vec::new()));
    let server = {
        let (calls, contents) = (calls.clone(), contents.clone());
        Server::start(move |request| {
            let mut calls = calls.lock().unwrap();
            calls.push(request.header("range").map(str::to_string));
            if calls.len() == 1 {
                Reply::Truncated { status: 200, length: total, body: contents[..256 * 1024].to_vec() }
            } else {
                let range = format!("bytes {}-{}/{}", 256 * 1024, total - 1, total);
                Reply::Full {
                    status: 206,
                    headers: vec![("Content-Range".into(), range)],
                    body: contents[256 * 1024..].to_vec(),
                }
            }
        })
        .await
    };
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("model.gguf");
    downloader()
        .download(&artifact(&server.url, &contents), &destination, &CancellationToken::new(), &quiet)
        .await
        .unwrap();
    assert_eq!(*calls.lock().unwrap(), [None, Some(format!("bytes={}-", 256 * 1024))]);
    assert_eq!(std::fs::read(&destination).unwrap(), contents);
}

#[tokio::test]
async fn temporary_http_failure_retries_but_permanent_error_does_not() {
    let calls = Arc::new(Mutex::new(0));
    let server = {
        let calls = calls.clone();
        Server::start(move |_| {
            let mut calls = calls.lock().unwrap();
            *calls += 1;
            if *calls == 1 { Reply::bytes(503, b"", &[("Retry-After", "0")]) } else { Reply::bytes(200, b"data", &[]) }
        })
        .await
    };
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("model.gguf");
    downloader()
        .download(&artifact(&server.url, b"data"), &destination, &CancellationToken::new(), &quiet)
        .await
        .unwrap();
    assert_eq!(*calls.lock().unwrap(), 2);

    let forbidden = Server::start(|_| Reply::status(403)).await;
    let error = downloader()
        .download(
            &artifact(&forbidden.url, b"more"),
            &directory.path().join("other.gguf"),
            &CancellationToken::new(),
            &quiet,
        )
        .await
        .unwrap_err();
    assert!(error.to_string().contains("403"));
    assert_eq!(forbidden.requests().len(), 1);
}

#[tokio::test]
async fn retry_exhaustion_preserves_partial_file_and_cancel_during_backoff() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    drop(listener);
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("model.gguf");
    std::fs::write(part(&destination), b"da").unwrap();
    let error = downloader()
        .download(&artifact(&url, b"data"), &destination, &CancellationToken::new(), &quiet)
        .await
        .unwrap_err();
    assert!(error.to_string().contains("часть сохранена"));
    assert_eq!(std::fs::read(part(&destination)).unwrap(), b"da");
    assert!(!destination.exists());

    let cancel = CancellationToken::new();
    let retries = Arc::new(Mutex::new(0));
    let progress = {
        let (cancel, retries) = (cancel.clone(), retries.clone());
        move |label: &str, _: u64, _: u64| {
            if label.starts_with("Повтор") {
                *retries.lock().unwrap() += 1;
                cancel.cancel();
            }
        }
    };
    let error = downloader().download(&artifact(&url, b"data"), &destination, &cancel, &progress).await.unwrap_err();
    assert!(error.is_cancelled());
    assert_eq!(*retries.lock().unwrap(), 1);
    assert_eq!(std::fs::read(part(&destination)).unwrap(), b"da");
}

#[tokio::test]
async fn large_download_uses_bounded_ranges_and_validates_complete_digest() {
    let contents = Arc::new(vec![b'x'; 33 * 1024 * 1024]);
    let calls = Arc::new(Mutex::new(Vec::new()));
    let server = {
        let (contents, calls) = (contents.clone(), calls.clone());
        Server::start(move |request| {
            let range = request.header("range").unwrap().strip_prefix("bytes=").unwrap();
            let (start, end) = range.split_once('-').unwrap();
            let (start, end): (usize, usize) = (start.parse().unwrap(), end.parse().unwrap());
            calls.lock().unwrap().push((start, end));
            let header = format!("bytes {start}-{end}/{}", contents.len());
            Reply::Full {
                status: 206,
                headers: vec![("Content-Range".into(), header)],
                body: contents[start..=end].to_vec(),
            }
        })
        .await
    };
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("engine.zip");
    downloader()
        .download(&artifact(&server.url, &contents), &destination, &CancellationToken::new(), &quiet)
        .await
        .unwrap();
    let calls = calls.lock().unwrap();
    assert_eq!(calls.len(), 3);
    assert_eq!(calls[0], (0, 16 * 1024 * 1024 - 1));
    assert_eq!(std::fs::read(&destination).unwrap(), *contents);
}
