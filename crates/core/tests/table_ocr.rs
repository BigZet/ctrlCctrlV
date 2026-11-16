use ctrlcctrlv_core::table_ocr::{recognition_prompt, rectangular_csv, table_result};

#[test]
fn converts_only_whole_rectangular_tables_and_preserves_literal_mode() {
    let md = "| A | B |\n| --- | --- |\n| x,y | \"z\" |\n| | 001 |";
    assert_eq!(table_result(md, "auto"), "A,B\n\"x,y\",\"\"\"z\"\"\"\n,001\n");
    assert_eq!(table_result(md, "text"), md);
    for source in ["prose\n|a|b|\n|---|---|\n|1|2|", "|a|b|\n|---|---|\n|1|", "f(a, b)\n    next()"] {
        assert_eq!(table_result(source, "auto"), source);
    }
}

#[test]
fn html_preserves_entities_empty_cells_and_multiline_values() {
    assert_eq!(table_result("<table><tr><td>a&amp;b<br>c</td><td></td></tr></table>", "csv"), "\"a&b\nc\",\n");
    for source in [
        "<table><tr><td colspan=\"2\">x</td></tr></table>",
        "<table><tr><td COLSPAN=\"2\">x</td></tr></table>",
        "<table><tr><td><script>x</script></td></tr></table>",
        "<table><tr><td>x</td></tr></table>prose",
        "<table><tr><td>x</tr></table>",
    ] {
        assert_eq!(table_result(source, "csv"), source);
    }
}

#[test]
fn lighton_wrapped_markdown_and_format_prompts() {
    let source = "<table>\n### Table - values\n*Type: data*\n|a|b|\n|---|---|\n|1|2|\n</table>";
    assert_eq!(table_result(source, "auto"), "a,b\n1,2\n");
    assert!(recognition_prompt("csv").contains("user selected a table"));
    assert_eq!(recognition_prompt("text"), ctrlcctrlv_core::ocr::PROMPT);
    assert!(rectangular_csv("a,b\n\"line\nbreak\",\n"));
    assert!(!rectangular_csv("a,b\n1,2,3\n"));
    assert!(!rectangular_csv("a,b\nx,\"unfinished"));
    assert!(!rectangular_csv("\"a\"garbage,b\nx,y"));
}
