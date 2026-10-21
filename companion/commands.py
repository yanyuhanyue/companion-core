"""Parse explicit QQ commands without treating quoted or similar text as controls."""

import re


def command_parts(text):
    match = re.fullmatch(r"/([^\s:：/]+)(?:[\s:：]+([\s\S]*))?", text.strip())
    return (match[1], (match[2] or "").strip()) if match else ("", "")


def is_memory_command(text):
    return command_parts(text)[0] in {
        "记住这个",
        "你记得什么",
        "确认这一条",
        "纠正这一条",
        "忘记这一条",
    }
