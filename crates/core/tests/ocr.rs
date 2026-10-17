mod support;

use std::time::Duration;

use ctrlcctrlv_core::catalog::PromptMode;
use ctrlcctrlv_core::ocr::{OcrProvider, Request, parse_completion, recognize_all};
use ctrlcctrlv_core::operation::Operation;
use ctrlcctrlv_core::{Error, lighton::clean_lighton_code};
use support::{Reply, Server, completion};

async fn endpoint(reply: impl Fn(&support::Request) -> Reply + Send + Sync + 'static) -> (Server, String) {
    let server = Server::start(reply).await;
    let url = format!("{}/v1/chat/completions", server.url);
    (server, url)
}

#[tokio::test]
async fn literal_code_and_blank_lines_survive_json_transport() {
    let expected = "    if x == \"Ж🙂\":\n\n\tprint(\"a|b\", '’')  \n";
    let (server, url) = endpoint(move |_| Reply::json(200, completion(expected, Some("stop"), false))).await;
    let operation = Operation::new();
    let text = OcrProvider::new(url, "model", "test-key").local().recognize(b"png", &operation).await.unwrap();
    let mut published = Vec::new();
    operation.publish(&text, |t| published.push(t.to_string())).unwrap();
    assert_eq!(published, [expected]);
    let request = &server.requests()[0];
    assert_eq!(request.header("authorization"), Some("Bearer test-key"));
    let payload = request.json();
    assert!(
        payload["messages"][0]["content"][1]["image_url"]["url"]
            .as_str()
            .unwrap()
            .starts_with("data:image/png;base64,")
    );
}

#[test]
fn incomplete_response_never_accepted() {
    for reason in [Some("length"), Some("content_filter"), Some("error"), None] {
        let error = parse_completion(&completion("partial", reason, false), PromptMode::Json).unwrap_err();
        assert!(error.to_string().contains("неполный"));
    }
}

#[test]
fn malformed_result_rejected() {
    for payload in [
        serde_json::json!({}),
        serde_json::json!({"choices": []}),
        completion("not-json", Some("stop"), true),
        serde_json::json!({"choices": [{"finish_reason": "stop", "message": {"content": "{\"text\": 42}"}}]}),
        serde_json::json!({"choices": [{"finish_reason": "stop", "message": {"content": 42}}]}),
    ] {
        assert!(matches!(parse_completion(&payload, PromptMode::Json), Err(Error::Operation(_))));
    }
}

#[test]
fn payload_matches_the_python_request() {
    let remote = OcrProvider::new("https://openrouter.ai/api/v1/chat/completions", "qwen/qwen3-vl-8b-instruct", "k");
    let payload = remote.payload(b"png", &Request::default());
    assert_eq!(payload["messages"][0]["content"][0]["text"], ctrlcctrlv_core::ocr::PROMPT);
    assert_eq!(payload["messages"][0]["content"][1]["image_url"]["url"], "data:image/png;base64,cG5n");
    assert_eq!(payload["temperature"], 0);
    assert_eq!(payload["modalities"], serde_json::json!(["text"]));
    assert_eq!(payload["response_format"], serde_json::json!({"type": "json_object"}));
    assert!(payload.get("chat_template_kwargs").is_none());
    let local =
        OcrProvider::new("http://127.0.0.1/x", "lightonocr-3-0.8b", "k").local().mode(PromptMode::Transcription);
    let payload = local.payload(b"png", &Request::default());
    assert_eq!(payload["messages"][0]["content"].as_array().unwrap().len(), 1);
    assert!(payload.get("response_format").is_none() && payload.get("modalities").is_none());
    assert_eq!(payload["chat_template_kwargs"], serde_json::json!({"enable_thinking": false}));
}

#[tokio::test]
async fn specialized_ocr_gets_image_only_and_literal_markdown() {
    let text = "```python\n    x = 1\n```\n";
    let (server, url) = endpoint(move |_| Reply::json(200, completion(text, Some("stop"), true))).await;
    let provider = OcrProvider::new(url, "other-transcriber", "test").local().mode(PromptMode::Transcription);
    assert_eq!(provider.recognize(b"png", &Operation::new()).await.unwrap(), text);
    let payload = server.requests()[0].json();
    assert!(payload.get("response_format").is_none());
    assert_eq!(payload["messages"][0]["content"][0]["type"], "image_url");
}

async fn lighton(raw: String) -> ctrlcctrlv_core::ocr::OcrResult {
    let (_server, url) = endpoint(move |_| Reply::json(200, completion(&raw, Some("stop"), true))).await;
    OcrProvider::new(url, "lightonocr-3-0.8b", "key")
        .local()
        .mode(PromptMode::Transcription)
        .recognize_result(b"png", &Operation::new(), &Request::default())
        .await
        .unwrap()
}

#[tokio::test]
async fn lighton_language_label_is_removed_before_publication() {
    let expected = "    def greet(name):\n\n\tprint(\"python\\n\", name)  \n";
    for prefix in ["python\n", "python\r\n", "python\\n"] {
        let result = lighton(format!("{prefix}{expected}")).await;
        assert_eq!(result.text, expected);
        assert_eq!(result.raw_text, format!("{prefix}{expected}"));
    }
}

#[test]
fn lighton_cleanup_preserves_literal_text_and_identifiers() {
    for text in [
        "python\nThis is a language name.\n",
        "python = 3\nprint(python)\n",
        "python\nfrom the documentation, choose the correct version.\n",
        "python\nif you need help, read the documentation.\n",
        "def f():\n    python = \"python\"\n",
        "python\n",
        "python\nhello world\n",
        "python\n```\ncode\n",
    ] {
        assert_eq!(clean_lighton_code(text), text, "{text:?}");
    }
}

#[test]
fn lighton_python_fence_is_unwrapped_without_stripping_code_whitespace() {
    assert_eq!(clean_lighton_code("```python\n\n    x = 1  \n\n```\n"), "\n    x = 1  \n\n");
    assert_eq!(clean_lighton_code("```Py\nx = 1\n```"), "x = 1\n");
    // A fence inside the body means the answer is not one wrapped block.
    let nested = "```python\na\n```\nb\n```\n";
    assert_eq!(clean_lighton_code(nested), nested);
}

#[test]
fn lighton_label_cleanup_preserves_numbered_code_and_configuration() {
    for expected in [
        "27  def main():\n28      parser = argparse.ArgumentParser()\n29      parser.parse_args()\n",
        "[build-system]\nrequires = [\"setuptools>=68\"]\nbuild-backend = \"setuptools.build_meta\"\n",
        "[build-system]\nrequires = [\"setuptools>=68\"]\n\n[project]\ninvalid OCR fragment\n",
        "import argparse\n\ndef main():\n    pass\n",
        "    x = compute()\n    y += 1\n",
        "print('hi')\n",
        "a.b.c(1)\n",
    ] {
        assert_eq!(clean_lighton_code(&format!("python\n{expected}")), expected, "{expected:?}");
    }
}

#[test]
fn deadline_before_publication_preserves_old_clipboard() {
    let started = tokio::time::Instant::now() - Duration::from_millis(4010);
    let operation = Operation::with_budget(Duration::from_secs(4)).started_at(started);
    let mut clipboard = "old".to_string();
    let error = operation.publish("new", |t| clipboard = t.to_string()).unwrap_err();
    assert_eq!(error, Error::DeadlineExceeded("OCR превысил 4 секунды. Буфер не изменён.".into()));
    assert_eq!(clipboard, "old");
}

#[test]
fn cancellation_before_publication_preserves_old_clipboard() {
    let operation = Operation::new();
    operation.cancel();
    let mut written = false;
    assert!(operation.publish("new", |_| written = true).unwrap_err().is_cancelled());
    assert!(!written);
    assert!(Operation::new().publish("  \n", |_| written = true).unwrap_err().to_string().contains("Текст не найден"));
    assert!(!written);
}

#[tokio::test]
async fn network_time_is_included_in_total_deadline() {
    let (server, url) = endpoint(|_| Reply::Stall { prefix: vec![] }).await;
    let operation = Operation::with_budget(Duration::from_secs(4))
        .started_at(tokio::time::Instant::now() - Duration::from_millis(3900));
    let error = OcrProvider::new(url, "model", "key").local().recognize(b"png", &operation).await.unwrap_err();
    assert!(matches!(error, Error::DeadlineExceeded(_)), "{error:?}");
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert!(server.was_disconnected());
}

#[tokio::test]
async fn sensitive_error_body_not_exposed() {
    let (_server, url) = endpoint(|_| Reply::bytes(401, b"private screenshot and key", &[])).await;
    let error =
        OcrProvider::new(url, "model", "secret").local().recognize(b"png", &Operation::new()).await.unwrap_err();
    let message = error.to_string();
    assert_eq!(message, "OCR HTTP 401. Проверьте API-ключ. Буфер не изменён.");
    assert!(!message.contains("private") && !message.contains("secret"));
}

#[tokio::test]
async fn http_400_reasons_are_classified() {
    for (body, reason) in [
        (r#"{"error":{"message":"response_format is not supported"}}"#, "JSON-формат"),
        (r#"{"error":{"message":"bad","metadata":{"raw":"Temperature must be 1"}}}"#, "temperature"),
        (r#"{"error":{"message":"Context length exceeded"}}"#, "лимит модели"),
        (r#"{"error":{"message":"No endpoints support image input"}}"#, "не принимает изображения"),
        ("not json", "Провайдер отклонил запрос. Обновите каталог"),
        (r#"{"error":{"message":"Unsupported   parameter: top_k"}}"#, "отклонил запрос: Unsupported parameter: top_k."),
    ] {
        let (_server, url) = endpoint(move |_| Reply::bytes(400, body.as_bytes(), &[])).await;
        let error = OcrProvider::new(url, "m", "k").local().recognize(b"png", &Operation::new()).await.unwrap_err();
        assert!(error.to_string().starts_with("OCR HTTP 400. "), "{error}");
        assert!(error.to_string().contains(reason), "{error}");
    }
}

#[tokio::test]
async fn speed_target_does_not_expire_unbounded_request_or_publication() {
    let (_server, url) =
        endpoint(|_| Reply::json(200, completion("full result after target", Some("stop"), false))).await;
    let operation = Operation::new().started_at(tokio::time::Instant::now() - Duration::from_secs(5));
    let text = OcrProvider::new(url, "model", "key").local().recognize(b"png", &operation).await.unwrap();
    let elapsed = operation.publish(&text, |_| {}).unwrap();
    assert!(elapsed > Duration::from_secs(4));
}

#[tokio::test]
async fn connection_failure_is_reported_without_details() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/chat/completions", listener.local_addr().unwrap());
    drop(listener);
    let error = OcrProvider::new(url, "model", "key").local().recognize(b"png", &Operation::new()).await.unwrap_err();
    assert_eq!(error.to_string(), "Не удалось подключиться к OCR. Буфер не изменён.");
}

#[tokio::test]
async fn cancel_closes_socket_while_server_is_not_sending_data() {
    for prefix in [Vec::new(), b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{".to_vec()] {
        let (server, url) = endpoint(move |_| Reply::Stall { prefix: prefix.clone() }).await;
        let operation = Operation::new();
        let provider = OcrProvider::new(url, "test", "key").local();
        let task = {
            let operation = operation.clone();
            tokio::spawn(async move { provider.recognize(b"png", &operation).await })
        };
        server.received.notified().await;
        let started = std::time::Instant::now();
        operation.cancel();
        let error = tokio::time::timeout(Duration::from_secs(1), task).await.unwrap().unwrap().unwrap_err();
        assert!(started.elapsed() < Duration::from_secs(1));
        assert!(error.is_cancelled());
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert!(server.was_disconnected());
    }
}

#[tokio::test]
async fn cancelled_operation_makes_no_request() {
    let (server, url) = endpoint(|_| Reply::status(500)).await;
    let operation = Operation::new();
    operation.cancel();
    let error = OcrProvider::new(url, "m", "k").local().recognize(b"png", &operation).await.unwrap_err();
    assert!(error.is_cancelled());
    assert!(server.requests().is_empty());
}

#[tokio::test]
async fn several_models_answer_the_same_capture_side_by_side() {
    let (server, url) = endpoint(|request| {
        let model = request.json()["model"].as_str().unwrap().to_string();
        match model.as_str() {
            "broken" => Reply::status(503),
            _ => Reply::json(200, completion(&format!("from {model}"), Some("stop"), false)),
        }
    })
    .await;
    let providers: Vec<OcrProvider> =
        ["a", "broken", "b"].iter().map(|model| OcrProvider::new(url.clone(), *model, "k").local()).collect();
    let results = recognize_all(&providers, b"png", &Operation::new(), &Request::default()).await;
    assert_eq!(results.len(), 3);
    assert_eq!(results[0].result.as_ref().unwrap().text, "from a");
    assert!(results[1].result.as_ref().unwrap_err().to_string().contains("HTTP 503"));
    assert_eq!(results[2].result.as_ref().unwrap().text, "from b");
    assert_eq!(server.requests().len(), 3);
}

#[tokio::test]
async fn review_context_is_sent_as_python_json_text() {
    let (server, url) = endpoint(|_| Reply::json(200, completion("ok", Some("stop"), false))).await;
    let context = serde_json::json!({"text": "Привет\n", "score": 0.5, "lines": [1, 2]});
    let request = Request { prompt: Some("Check"), context: Some(&context) };
    OcrProvider::new(url, "m", "k").local().recognize_result(b"png", &Operation::new(), &request).await.unwrap();
    let content = &server.requests()[0].json()["messages"][0]["content"];
    assert_eq!(content[0]["text"], "Check");
    assert_eq!(content[2]["text"], "{\"text\": \"Привет\\n\", \"score\": 0.5, \"lines\": [1, 2]}");
}

#[tokio::test]
async fn streams_partial_text_and_checks_the_final_answer() {
    let events = [
        r#"{"choices":[{"delta":{"content":"{\"text\": \"line 1\\n"}}]}"#,
        r#"{"choices":[{"delta":{"content":"line 2\"}"},"finish_reason":"stop"}]}"#,
    ];
    let body: String = events.iter().map(|event| format!("data: {event}\n\n")).collect::<String>() + "data: [DONE]\n\n";
    let (server, url) =
        endpoint(move |_| Reply::bytes(200, body.as_bytes(), &[("Content-Type", "text/event-stream")])).await;
    let seen = std::sync::Mutex::new(Vec::new());
    let partial = |text: &str| seen.lock().unwrap().push(text.to_string());
    let result = OcrProvider::new(url, "model", "k")
        .local()
        .recognize_stream(b"png", &Operation::new(), &Request::default(), &partial)
        .await
        .unwrap();
    assert_eq!(result.text, "line 1\nline 2");
    assert_eq!(seen.lock().unwrap().last().map(String::as_str), Some("line 1\nline 2"));
    assert_eq!(server.requests()[0].json()["stream"], true);
}

#[tokio::test]
async fn stream_falls_back_to_a_plain_answer_and_rejects_cut_streams() {
    let (_server, url) = endpoint(|_| Reply::json(200, completion("plain", Some("stop"), false))).await;
    let provider = OcrProvider::new(url, "model", "k").local();
    let result = provider.recognize_stream(b"png", &Operation::new(), &Request::default(), &|_| {}).await.unwrap();
    assert_eq!(result.text, "plain");

    let body = "data: {\"choices\":[{\"delta\":{\"content\":\"{\\\"text\\\": \\\"cut\"}}]}\n\n";
    let (_server, url) =
        endpoint(move |_| Reply::bytes(200, body.as_bytes(), &[("Content-Type", "text/event-stream")])).await;
    let provider = OcrProvider::new(url, "model", "k").local();
    let error = provider.recognize_stream(b"png", &Operation::new(), &Request::default(), &|_| {}).await.unwrap_err();
    assert!(error.to_string().contains("неполный"));

    let (_server, url) = endpoint(|_| Reply::json(401, serde_json::json!({}))).await;
    let provider = OcrProvider::new(url, "model", "k").local();
    let error = provider.recognize_stream(b"png", &Operation::new(), &Request::default(), &|_| {}).await.unwrap_err();
    assert!(error.to_string().contains("401"));
}

#[test]
fn names_the_upstream_provider_that_refused_the_request() {
    let body =
        br#"{"error": {"code": 401, "message": "secret prompt text", "metadata": {"provider_name": "Anthropic"}}}"#;
    let message = ctrlcctrlv_core::ocr::http_error(401, body);
    assert!(message.contains("от провайдера Anthropic"), "{message}");
    assert!(!message.contains("secret"));
    // Without a plain provider name the message stays the Python app's.
    let odd = br#"{"error": {"metadata": {"provider_name": "<script>"}}}"#;
    assert_eq!(ctrlcctrlv_core::ocr::http_error(401, odd), "OCR HTTP 401. Проверьте API-ключ. Буфер не изменён.");
    assert_eq!(ctrlcctrlv_core::ocr::http_error(401, b""), "OCR HTTP 401. Проверьте API-ключ. Буфер не изменён.");
}
