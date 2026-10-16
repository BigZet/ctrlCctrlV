//! LightOnOCR wraps recognised Python code in a language label or a Markdown
//! fence. Remove that presentation wrapper, preserving the code bytes, and
//! never strip a language name from prose.

use std::sync::LazyLock;

use regex::Regex;

static FENCED: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?ims)\A```(?:python|python3|py)[ \t]*\r?\n(.*?)^```[ \t]*(?:\r?\n)?\z").unwrap());
static FENCE_LINE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?m)^```").unwrap());
static LABEL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\A(?:python|python3|py)[ \t]*(?:\r?\n|\\n)").unwrap());
static LINE_NUMBER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?m)^[ \t]*\d+[ \t]").unwrap());
static CODE_START: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"\A\s*(?:(?:async\s+)?def\s+\w+\s*\(|class\s+\w+\s*[:(]|from\s+[\w.]+\s+import\s|",
        r"(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*\s*\()"
    ))
    .unwrap()
});

pub fn clean_lighton_code(text: &str) -> String {
    if let Some(body) = FENCED.captures(text).and_then(|c| c.get(1))
        && !FENCE_LINE.is_match(body.as_str())
    {
        return body.as_str().to_string();
    }
    let Some(label) = LABEL.find(text) else {
        return text.to_string();
    };
    let code = &text[label.end()..];
    // Line numbers are ignored only for detection; the returned body stays literal.
    let candidate = LINE_NUMBER.replace_all(code, "");
    // Incomplete snippets also count as code.
    if CODE_START.is_match(&candidate) || has_python_statements(&dedent(&candidate)) || opens_toml(code) {
        return code.to_string();
    }
    text.to_string()
}

/// True when the text parses as Python and contains a statement other than a
/// bare non-call expression (the rule the Python app applies to `ast.parse`).
fn has_python_statements(source: &str) -> bool {
    if source.contains('\0') {
        return false;
    }
    let mut parser = tree_sitter::Parser::new();
    if parser.set_language(&tree_sitter_python::LANGUAGE.into()).is_err() {
        return false;
    }
    let Some(tree) = parser.parse(source, None) else {
        return false;
    };
    let root = tree.root_node();
    if root.has_error() {
        return false;
    }
    let mut cursor = root.walk();
    root.named_children(&mut cursor).any(|node| match node.kind() {
        "comment" => false,
        // Python 2 statements are syntax errors for `ast`.
        "print_statement" | "exec_statement" => false,
        "expression_statement" => {
            let mut inner = node.walk();
            let children: Vec<_> = node.named_children(&mut inner).filter(|c| c.kind() != "comment").collect();
            match children.as_slice() {
                [only] => matches!(only.kind(), "call" | "assignment" | "augmented_assignment"),
                // `a, b` is one tuple expression for `ast`.
                _ => false,
            }
        }
        _ => true,
    })
}

/// A TOML table header at the start, even when later OCR has syntax errors.
fn opens_toml(code: &str) -> bool {
    let opening: Vec<&str> = code.lines().filter(|line| !line.trim().is_empty()).take(2).collect();
    match opening.join("\n").parse::<toml::Table>() {
        Ok(table) => table.values().any(|value| value.as_table().is_some_and(|t| !t.is_empty())),
        Err(_) => false,
    }
}

/// Python's `textwrap.dedent`: removes the longest common leading whitespace;
/// whitespace-only lines are normalised to empty lines.
fn dedent(text: &str) -> String {
    let lines: Vec<&str> = text.split_inclusive('\n').collect();
    let blank = |line: &str| line.trim_matches(|c| c == ' ' || c == '\t' || c == '\n' || c == '\r').is_empty();
    let mut margin: Option<&str> = None;
    for line in lines.iter().filter(|line| !blank(line)) {
        let indent = &line[..line.len() - line.trim_start_matches([' ', '\t']).len()];
        margin = Some(match margin {
            None => indent,
            Some(current) => {
                let common = current.bytes().zip(indent.bytes()).take_while(|(a, b)| a == b).count();
                &current[..common]
            }
        });
    }
    let margin = margin.unwrap_or("");
    lines
        .iter()
        .map(|line| if blank(line) { if line.ends_with('\n') { "\n" } else { "" } } else { &line[margin.len()..] })
        .collect()
}
