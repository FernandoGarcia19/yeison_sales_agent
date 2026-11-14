"""
The model writes Markdown; WhatsApp has its own marks (*bold*, _italic_, ~strike~) and shows the
rest as literal symbols. Convert what the model usually produces before sending.
"""

import re

_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t#]*$", re.MULTILINE)
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BULLET = re.compile(r"^([ \t]*)[*+][ \t]+", re.MULTILINE)


def to_whatsapp(text: str) -> str:
    if not text:
        return text
    text = _LINK.sub(lambda m: m.group(2) if m.group(1) == m.group(2) else f"{m.group(1)}: {m.group(2)}", text)
    text = _HEADING.sub(r"*\1*", text)
    # A list item written with "*" would be read as the start of a bold
    text = _BULLET.sub(r"\1- ", text)
    text = _BOLD.sub(lambda m: f"*{m.group(1) or m.group(2)}*", text)
    text = _STRIKE.sub(r"~\1~", text)
    # "***x***" leaves "**x**" behind after one pass
    return text.replace("**", "*")
