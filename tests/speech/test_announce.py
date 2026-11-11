from __future__ import annotations

from openjarvis.speech.announce import speakable


def test_strips_markdown_code_and_urls() -> None:
    text = """\
## Result

I checked **three** games and `2` need updates.

```powershell
Get-Process
```

More detail at https://example.com/very/long/path and [this link](https://x.test).
"""
    spoken = speakable(text)
    assert "**" not in spoken
    assert "```" not in spoken
    assert "http" not in spoken
    assert "Get-Process" not in spoken
    assert "checked three games" in spoken


def test_keeps_it_short() -> None:
    text = "First sentence. " * 40
    spoken = speakable(text, max_chars=120)
    assert len(spoken) <= 121


def test_empty_input() -> None:
    assert speakable("") == ""
    assert speakable("```\nonly code\n```") == ""
