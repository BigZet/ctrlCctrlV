//! Test double for `llama-server`: accepts the same arguments, checks the API
//! key file and answers `/health` and chat completions. Used by the runtime
//! tests only; it never loads a model.

use std::io::{Read, Write};
use std::net::TcpListener;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let value = |name: &str| args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned();
    if args.iter().any(|a| a == "--list-devices") {
        println!("Available devices:\n  CUDA0: Fake GPU (24000 MiB, 23000 MiB free)");
        return;
    }
    let model = value("-m").unwrap_or_default();
    if model.contains("crash") {
        std::process::exit(3);
    }
    let port: u16 = value("--port").and_then(|p| p.parse().ok()).expect("--port");
    let key = std::fs::read_to_string(value("--api-key-file").expect("--api-key-file")).expect("key file");
    let listener = TcpListener::bind(("127.0.0.1", port)).expect("bind");
    for stream in listener.incoming() {
        let Ok(mut stream) = stream else { continue };
        let mut data = Vec::new();
        let mut buffer = [0u8; 65536];
        let head_end = loop {
            if let Some(p) = data.windows(4).position(|w| w == b"\r\n\r\n") {
                break p;
            }
            match stream.read(&mut buffer) {
                Ok(0) | Err(_) => break usize::MAX,
                Ok(n) => data.extend_from_slice(&buffer[..n]),
            }
        };
        if head_end == usize::MAX {
            continue;
        }
        let head = String::from_utf8_lossy(&data[..head_end]).to_lowercase();
        let length: usize = head
            .lines()
            .find_map(|l| l.strip_prefix("content-length:"))
            .and_then(|v| v.trim().parse().ok())
            .unwrap_or(0);
        while data.len() < head_end + 4 + length {
            match stream.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(n) => data.extend_from_slice(&buffer[..n]),
            }
        }
        let authorized = head.contains(&format!("authorization: bearer {}", key.to_lowercase()));
        let (status, body) = if !authorized {
            ("401 Unauthorized", "{}".to_string())
        } else if head.starts_with("get /health") {
            ("200 OK", r#"{"status":"ok"}"#.to_string())
        } else {
            (
                "200 OK",
                r#"{"choices":[{"finish_reason":"stop","message":{"content":"{\"text\": \"warm\"}"}}]}"#.to_string(),
            )
        };
        let _ =
            write!(stream, "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    }
}
