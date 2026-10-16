//! A minimal HTTP/1.1 server for tests, with control over partial and stalled responses.
#![allow(dead_code)]

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[derive(Debug, Clone)]
pub struct Request {
    pub method: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl Request {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)).map(|(_, value)| value.as_str())
    }

    pub fn json(&self) -> serde_json::Value {
        serde_json::from_slice(&self.body).unwrap()
    }
}

pub enum Reply {
    Full {
        status: u16,
        headers: Vec<(String, String)>,
        body: Vec<u8>,
    },
    /// Sends headers declaring `length` bytes, then `body`, then closes.
    Truncated {
        status: u16,
        length: usize,
        body: Vec<u8>,
    },
    /// Sends `prefix` (possibly nothing) and waits until the client disconnects.
    Stall {
        prefix: Vec<u8>,
    },
}

impl Reply {
    pub fn json(status: u16, value: serde_json::Value) -> Self {
        Reply::Full {
            status,
            headers: vec![("Content-Type".into(), "application/json".into())],
            body: serde_json::to_vec(&value).unwrap(),
        }
    }

    pub fn status(status: u16) -> Self {
        Reply::Full { status, headers: vec![], body: vec![] }
    }

    pub fn bytes(status: u16, body: &[u8], headers: &[(&str, &str)]) -> Self {
        Reply::Full {
            status,
            headers: headers.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect(),
            body: body.to_vec(),
        }
    }
}

pub struct Server {
    pub url: String,
    pub requests: Arc<Mutex<Vec<Request>>>,
    /// Set once a stalled connection saw the client disconnect.
    pub disconnected: Arc<AtomicBool>,
    /// Set once a request has been fully received.
    pub received: Arc<tokio::sync::Notify>,
}

impl Server {
    pub async fn start(handler: impl Fn(&Request) -> Reply + Send + Sync + 'static) -> Server {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let requests = Arc::new(Mutex::new(Vec::new()));
        let disconnected = Arc::new(AtomicBool::new(false));
        let received = Arc::new(tokio::sync::Notify::new());
        let handler = Arc::new(handler);
        let (log, gone, notify) = (requests.clone(), disconnected.clone(), received.clone());
        tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else {
                    return;
                };
                let (handler, log, gone, notify) = (handler.clone(), log.clone(), gone.clone(), notify.clone());
                tokio::spawn(async move {
                    loop {
                        let Some(request) = read_request(&mut socket).await else {
                            return;
                        };
                        log.lock().unwrap().push(request.clone());
                        notify.notify_one();
                        match handler(&request) {
                            Reply::Full { status, headers, body } => {
                                let mut head = format!("HTTP/1.1 {status} X\r\nContent-Length: {}\r\n", body.len());
                                for (key, value) in headers {
                                    head.push_str(&format!("{key}: {value}\r\n"));
                                }
                                head.push_str("\r\n");
                                if socket.write_all(head.as_bytes()).await.is_err()
                                    || socket.write_all(&body).await.is_err()
                                {
                                    return;
                                }
                            }
                            Reply::Truncated { status, length, body } => {
                                let head = format!("HTTP/1.1 {status} X\r\nContent-Length: {length}\r\n\r\n");
                                let _ = socket.write_all(head.as_bytes()).await;
                                let _ = socket.write_all(&body).await;
                                let _ = socket.shutdown().await;
                                return;
                            }
                            Reply::Stall { prefix } => {
                                let _ = socket.write_all(&prefix).await;
                                let mut byte = [0u8; 1];
                                if matches!(socket.read(&mut byte).await, Ok(0) | Err(_)) {
                                    gone.store(true, Ordering::SeqCst);
                                }
                                return;
                            }
                        }
                    }
                });
            }
        });
        Server { url, requests, disconnected, received }
    }

    pub fn requests(&self) -> Vec<Request> {
        self.requests.lock().unwrap().clone()
    }

    pub fn was_disconnected(&self) -> bool {
        self.disconnected.load(Ordering::SeqCst)
    }
}

async fn read_request(socket: &mut tokio::net::TcpStream) -> Option<Request> {
    let mut data = Vec::new();
    let mut buffer = [0u8; 65536];
    let end = loop {
        if let Some(position) = data.windows(4).position(|w| w == b"\r\n\r\n") {
            break position;
        }
        let read = socket.read(&mut buffer).await.ok()?;
        if read == 0 {
            return None;
        }
        data.extend_from_slice(&buffer[..read]);
    };
    let head = String::from_utf8_lossy(&data[..end]).to_string();
    let mut lines = head.split("\r\n");
    let mut first = lines.next()?.split(' ');
    let method = first.next()?.to_string();
    let path = first.next()?.to_string();
    let headers: Vec<(String, String)> = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))
        .collect();
    let length: usize = headers
        .iter()
        .find(|(k, _)| k.eq_ignore_ascii_case("content-length"))
        .and_then(|(_, v)| v.parse().ok())
        .unwrap_or(0);
    let mut body = data[end + 4..].to_vec();
    while body.len() < length {
        let read = socket.read(&mut buffer).await.ok()?;
        if read == 0 {
            return None;
        }
        body.extend_from_slice(&buffer[..read]);
    }
    Some(Request { method, path, headers, body })
}

pub fn completion(text: &str, reason: Option<&str>, raw: bool) -> serde_json::Value {
    let content = if raw { text.to_string() } else { serde_json::json!({"text": text}).to_string() };
    serde_json::json!({"choices": [{"finish_reason": reason, "message": {"content": content}}]})
}
