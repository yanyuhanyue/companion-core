"""Parse explicit QQ commands without treating quoted or similar text as controls."""

import re


def command_parts(text):
    match = re.fullmatch(r"/([^\s:：/]+)(?:[\s:：]+([\s\S]*))?", text.strip())
    return (match[1], (match[2] or "").strip()) if match else ("", "")
