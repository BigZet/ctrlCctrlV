//! Streamed chat completions (server-sent events) and the partial text shown
//! while an answer is still arriving.

use std::time::Duration;

use serde_json::Value;

use crate::catalog::PromptMode;
use crate::error::{Error, Result};

/// Called with the whole text recognized so far, not with the newest piece.
pub type Partial<'a> = &'a (dyn Fn(&str) + Send + Sync);

/// How often the partial text is decoded and reported while tokens arrive.
pub(crate) const PARTIAL_INTERVAL: Duration = Duration::from_millis(60);

const MAX_CONTENT: usize = 2_000_000;
const MAX_LINE: usize = 4_000_000;

pub(crate) enum Streamed {
    /// The concatenated `delta.content` of a stream that finished with `stop`.
    Content(String),
    /// A plain JSON body from a server that does not stream.
    Whole(Vec<u8>),
}

/// Collects `data:` events of an OpenAI-compatible stream.
pub(crate) struct SseParser {
    mode: PromptMode,
    line: Vec<u8>,
    content: String,
    finish: Option<String>,
    done: bool,
}

impl SseParser {
    pub fn new(mode: PromptMode) -> Self {
        Self { mode, line: Vec::new(), content: String::new(), finish: None, done: false }
    }

    /// Consumes bytes; returns true once the stream says it is done.
    pub fn feed(&mut self, bytes: &[u8]) -> Result<bool> {
        for &byte in bytes {
            if byte != b'\n' {
                self.line.push(byte);
                if self.line.len() > MAX_LINE {
                    return Err(Error::operation("Слишком большой ответ OCR"));
                }
                continue;
            }
            let line = std::mem::take(&mut self.line);
            self.event(&line)?;
            if self.done {
                return Ok(true);
            }
        }
        Ok(false)
    }

    fn event(&mut self, line: &[u8]) -> Result<()> {
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        // Comments (`: OPENROUTER PROCESSING`), event names and blank separators.
        let Some(data) = line.strip_prefix(b"data:") else { return Ok(()) };
        let data = data.trim_ascii();
        if data == b"[DONE]" {
            self.done = true;
            return Ok(());
        }
        let Ok(value) = serde_json::from_slice::<Value>(data) else { return Ok(()) };
        if value.get("error").is_some_and(|error| !error.is_null()) {
            // Never echo the provider's message: it may quote the request.
            return Err(Error::operation("Провайдер прервал ответ OCR. Буфер не изменён."));
        }
        let Some(choice) = value.get("choices").and_then(|c| c.get(0)) else { return Ok(()) };
        if let Some(piece) = choice.get("delta").and_then(|d| d.get("content")).and_then(Value::as_str) {
            self.content.push_str(piece);
            if self.content.len() > MAX_CONTENT {
                return Err(Error::operation("Слишком большой ответ OCR"));
            }
        }
        if let Some(reason) = choice.get("finish_reason").and_then(Value::as_str) {
            self.finish = Some(reason.to_string());
        }
        Ok(())
    }

    /// What the user should see so far.
    pub fn visible(&self) -> String {
        match self.mode {
            PromptMode::Transcription => self.content.clone(),
            PromptMode::Json => partial_json_text(&self.content),
        }
    }

    /// The whole content, accepted only if the model finished on its own.
    pub fn finish(&mut self) -> Result<String> {
        if !self.line.is_empty() {
            let line = std::mem::take(&mut self.line);
            self.event(&line)?;
        }
        if self.finish.as_deref() != Some("stop") {
            return Err(Error::operation("Ответ OCR неполный. Буфер не изменён."));
        }
        Ok(self.content.clone())
    }
}

/// The value of `"text"` in a JSON object that may still be incomplete:
/// `{"text": "a\nb` gives `a` and `b` on two lines.
pub fn partial_json_text(json: &str) -> String {
    let Some(key) = json.find("\"text\"") else { return String::new() };
    let rest = json[key + 6..].trim_start();
    let Some(rest) = rest.strip_prefix(':') else { return String::new() };
    let Some(rest) = rest.trim_start().strip_prefix('"') else { return String::new() };
    let mut text = String::with_capacity(rest.len());
    let mut chars = rest.chars();
    while let Some(c) = chars.next() {
        match c {
            '"' => break,
            '\\' => {
                let Some(escape) = chars.next() else { break };
                let decoded = match escape {
                    'n' => '\n',
                    't' => '\t',
                    'r' => '\r',
                    'b' => '\u{8}',
                    'f' => '\u{c}',
                    'u' => {
                        let Some(unit) = hex4(&mut chars) else { break };
                        if (0xD800..0xDC00).contains(&unit) {
                            // A surrogate pair; wait for the second half.
                            let mut ahead = chars.clone();
                            let (Some('\\'), Some('u'), Some(low)) = (ahead.next(), ahead.next(), hex4(&mut ahead))
                            else {
                                break;
                            };
                            chars = ahead;
                            let code = 0x10000 + ((unit - 0xD800) << 10) + (low.wrapping_sub(0xDC00) & 0x3FF);
                            char::from_u32(code).unwrap_or('\u{FFFD}')
                        } else {
                            char::from_u32(unit).unwrap_or('\u{FFFD}')
                        }
                    }
                    other => other,
                };
                text.push(decoded);
            }
            c => text.push(c),
        }
    }
    text
}

fn hex4(chars: &mut std::str::Chars<'_>) -> Option<u32> {
    let mut value = 0;
    for _ in 0..4 {
        value = value * 16 + chars.next()?.to_digit(16)?;
    }
    Some(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_incomplete_json_text() {
        assert_eq!(partial_json_text(""), "");
        assert_eq!(partial_json_text("{\"te"), "");
        assert_eq!(partial_json_text("{\"text\": \""), "");
        assert_eq!(partial_json_text("{\"text\": \"a\\nb\\\"c"), "a\nb\"c");
        assert_eq!(partial_json_text("{\"text\":\"done\"}"), "done");
        // An escape cut in half shows nothing of it yet.
        assert_eq!(partial_json_text("{\"text\": \"x\\"), "x");
        assert_eq!(partial_json_text("{\"text\": \"x\\u04"), "x");
        assert_eq!(partial_json_text("{\"text\": \"\\u0416\\ud83d"), "Ж");
        assert_eq!(partial_json_text("{\"text\": \"\\u0416\\ud83d\\ude42!"), "Ж🙂!");
    }

    #[test]
    fn collects_events_split_anywhere() {
        let stream = concat!(
            ": OPENROUTER PROCESSING\n\n",
            "data: {\"choices\":[{\"delta\":{\"role\":\"assistant\",\"content\":\"{\\\"text\\\": \\\"he\"}}]}\r\n\r\n",
            "data: {\"choices\":[{\"delta\":{\"content\":\"llo\\\"}\"},\"finish_reason\":null}]}\n\n",
            "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
            "data: [DONE]\n\n",
        );
        for split in [1, 7, 50, stream.len()] {
            let mut parser = SseParser::new(PromptMode::Json);
            let mut done = false;
            for chunk in stream.as_bytes().chunks(split) {
                done = parser.feed(chunk).unwrap();
                if done {
                    break;
                }
            }
            assert!(done);
            assert_eq!(parser.visible(), "hello");
            assert_eq!(parser.finish().unwrap(), "{\"text\": \"hello\"}");
        }
    }

    #[test]
    fn rejects_cut_or_failed_streams() {
        let mut parser = SseParser::new(PromptMode::Transcription);
        parser.feed(b"data: {\"choices\":[{\"delta\":{\"content\":\"abc\"}}]}\n").unwrap();
        assert_eq!(parser.visible(), "abc");
        assert!(parser.finish().unwrap_err().to_string().contains("неполный"));
        let mut parser = SseParser::new(PromptMode::Transcription);
        parser.feed(b"data: {\"choices\":[{\"delta\":{\"content\":\"a\"},\"finish_reason\":\"length\"}]}\n").unwrap();
        assert!(parser.finish().is_err());
        let mut parser = SseParser::new(PromptMode::Transcription);
        let error = parser.feed(b"data: {\"error\":{\"message\":\"secret prompt\"}}\n").unwrap_err();
        assert!(!error.to_string().contains("secret"));
        // The last event may come without a trailing newline.
        let mut parser = SseParser::new(PromptMode::Transcription);
        parser.feed(b"data: {\"choices\":[{\"delta\":{\"content\":\"z\"},\"finish_reason\":\"stop\"}]}").unwrap();
        assert_eq!(parser.finish().unwrap(), "z");
    }
}
