#!/usr/bin/env python3
"""Render a Slack payload as a static HTML preview.

Reviewing the message before it pages someone is part of the workflow: run the
CLI with ``--payload-only``, then render it here. It also produces the
screenshot used in the case study.

    python -m aiops_alerts.cli --job X --log-file Y --payload-only > /tmp/p.json
    python tools/preview_slack.py /tmp/p.json docs/screenshots/slack-alert.html
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

MENTION = re.compile(r"<@([A-Z0-9]+)>")
LINK = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]+))?>")
BOLD = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
CODE = re.compile(r"`([^`\n]+)`")
FENCE = re.compile(r"```(.*?)```", re.DOTALL)
EMOJI = re.compile(r":([a-z0-9_+-]+):")

EMOJI_MAP = {
    "warning": "⚠️",
    "rotating_light": "🚨",
    "white_check_mark": "✅",
    "information_source": "ℹ️",
}


def mrkdwn_to_html(text: str) -> str:
    """Convert the subset of Slack mrkdwn that alerts actually use.

    Markup is replaced with placeholders *before* escaping, otherwise the
    angle-bracket syntax Slack uses for mentions and links would be escaped
    into literal text.
    """
    placeholders: list[str] = []

    def stash(markup: str) -> str:
        placeholders.append(markup)
        return f"\x00{len(placeholders) - 1}\x00"

    text = FENCE.sub(lambda match: stash(f"<pre>{html.escape(match.group(1).strip())}</pre>"), text)
    text = CODE.sub(lambda match: stash(f"<code>{html.escape(match.group(1))}</code>"), text)
    text = MENTION.sub(lambda match: stash(f'<span class="mention">@{html.escape(match.group(1))}</span>'), text)
    text = LINK.sub(
        lambda match: stash(
            f'<a href="{html.escape(match.group(1))}">{html.escape(match.group(2) or match.group(1))}</a>'
        ),
        text,
    )

    text = html.escape(text)
    text = BOLD.sub(r"<strong>\1</strong>", text)
    text = EMOJI.sub(lambda match: EMOJI_MAP.get(match.group(1), f":{match.group(1)}:"), text)
    text = text.replace("\n", "<br>")

    for index, markup in enumerate(placeholders):
        text = text.replace(f"\x00{index}\x00", markup)
    return text


def render(payload: dict, title: str = "Slack preview") -> str:
    blocks = payload.get("blocks", [])
    body: list[str] = []

    for block in blocks:
        kind = block.get("type")
        if kind == "header":
            body.append(f'<p class="header">{html.escape(block["text"]["text"])}</p>')
        elif kind == "section":
            body.append(f'<p class="section">{mrkdwn_to_html(block["text"]["text"])}</p>')
        elif kind == "context":
            text = " ".join(element.get("text", "") for element in block.get("elements", []))
            body.append(f'<p class="context">{mrkdwn_to_html(text)}</p>')
        elif kind == "divider":
            body.append('<hr class="divider">')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px; background: #f4f5f7;
    font-family: "Lato", -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    color: #1d1c1d; font-size: 15px; line-height: 1.46;
  }}
  .slack {{
    width: 760px; margin: 0 auto; background: #ffffff;
    border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;
  }}
  .chrome {{
    display: flex; align-items: center; gap: 8px;
    padding: 10px 16px; border-bottom: 1px solid #ececec; background: #fbfbfb;
  }}
  .dot {{ width: 11px; height: 11px; border-radius: 50%; }}
  .dot--r {{ background: #ff5f57; }} .dot--y {{ background: #febc2e; }} .dot--g {{ background: #28c840; }}
  .chrome span {{ color: #616061; font-size: 12.5px; margin-left: 6px; }}
  .message {{ padding: 18px 20px 22px; }}
  .identity {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }}
  .avatar {{
    width: 38px; height: 38px; border-radius: 6px; background: #0d1520; color: #7ee787;
    display: grid; place-items: center; font-weight: 700; font-size: 13px; letter-spacing: .5px;
  }}
  .name {{ font-weight: 700; font-size: 15px; }}
  .badge {{
    background: #ececec; color: #616061; font-size: 10.5px; font-weight: 700;
    border-radius: 3px; padding: 1px 4px; margin-left: 6px; vertical-align: 1px;
  }}
  .time {{ color: #616061; font-size: 12.5px; margin-left: 6px; }}
  .header {{ font-size: 17px; font-weight: 800; margin: 0 0 12px; }}
  .section {{ margin: 0 0 12px; }}
  .context {{ color: #616061; font-size: 12.5px; margin: 4px 0 0; }}
  .mention {{
    background: #e8f5fa; color: #1264a3; border-radius: 3px; padding: 0 3px; font-weight: 600;
  }}
  code {{
    background: #f8f8f8; border: 1px solid #e8e8e8; border-radius: 3px;
    padding: 1px 4px; font-family: "SF Mono", Menlo, monospace; font-size: 12.5px;
  }}
  pre {{
    background: #f8f8f8; border: 1px solid #e8e8e8; border-left: 3px solid #dddddd;
    border-radius: 4px; padding: 8px 10px; margin: 6px 0 0;
    font-family: "SF Mono", Menlo, monospace; font-size: 12.5px; line-height: 1.5;
    white-space: pre-wrap;
  }}
  a {{ color: #1264a3; text-decoration: none; }}
  hr.divider {{ border: none; border-top: 1px solid #ececec; margin: 12px 0; }}
</style>
</head>
<body>
  <div class="slack">
    <div class="chrome">
      <span class="dot dot--r"></span><span class="dot dot--y"></span><span class="dot dot--g"></span>
      <span>#data-alerts — Slack</span>
    </div>
    <div class="message">
      <div class="identity">
        <div class="avatar">AIOps</div>
        <div>
          <span class="name">Pipeline Alerts</span><span class="badge">APP</span>
          <span class="time">today at 2:31 AM</span>
        </div>
      </div>
      {"".join(body)}
    </div>
  </div>
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text())
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(payload))
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
