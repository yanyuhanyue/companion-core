# QQ references, quotes and cards (A1)

`rich_input_scope="references_v1"` is an explicit, default-off input interpretation
scope. The bounded window pins it in `LiveTextWindowApproval`; changing that scope
changes the plan and the approved configuration. The persistent Bridge and Core
each have a separate `rich_input_scope` setting, defaulting to `none`. Enabling it
does not grant model, delivery, Content, media download or memory-write authority.
The original Spool, signed capture receipt, Budget, Outbox and Window Journal remain
the only input/accounting/delivery path. The current implementation also reads
quoted text through bounded `get_msg` / `get_forward_msg` calls for references in
the authenticated owner's private input. It is not a general message-reading tool.

`payload.text` is the only current-owner text. In a segment array it concatenates
only `text.data.text`; a string is literal text, including CQ-looking syntax.
Optional `payload.rich` has `version="references_v1"`, `coverage="PARTIAL"`,
`segment_count`, `owner_text_positions` and an ordered `segments` list.
`segment_count` is a strict integer from 1 to 64. `owner_text_positions` is the
strictly increasing list of original text-segment indices. Its indices and the
non-text indices partition the complete original segment range without gaps or
overlap. Core and Bridge independently validate that partition. No second owner
body is stored; text-only messages still omit `rich` and keep their original hash.
Each non-text entry has the original zero-based `index`, `type`,
`actor="UNVERIFIED"` and a coverage code:

| Push segment | Retained projection | Coverage |
| --- | --- | --- |
| `reply` | Signed decimal message ID in `reference`, optional bounded `body` | Baseline `MISSING`; `body.status` records the read result |
| `forward` | Bounded opaque ASCII ID in `reference`, optional bounded `body` | Baseline `UNFETCHED`; `body.status` records the read result |
| `json` | Small title/summary/URL field projection with original JSON pointers | `PARTIAL`, or `UNSUPPORTED` |
| `node` | Ordered text and unverified display names in bounded `body` | Baseline `UNSUPPORTED`; `body.status` records the read result |
| image/record/video/file, other | Type/index only; no media URL | `UNSUPPORTED`, unless the separate current-owner image scope applies |

The push-shape basis is SnowLuma commit
[`db142db9e6398c779283eab9afc3552da1e2c361`](https://github.com/SnowLuma/SnowLuma/tree/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/event-converter).
Its ordinary `to-message` path calls `elementsToOneBotSegments` without
`includeReplyContent`; the reply/forward codecs emit only `data.id`, and the JSON
codec emits an opaque `data.data` string. There is no ordinary node codec. This is
public-source evidence, not proof of a particular deployed image's source hash.

Card projection recognizes explicit `/title`, `/desc`, `/summary`, `/prompt`, `/url`,
`/jumpUrl`, and supported title/desc/summary/URL fields below `/meta/detail_1`,
`/meta/detail`, `/meta/news` and `/meta/music`.
These are conservative extraction paths in opaque JSON, not a claim that every
SnowLuma card uses this schema. Fields remain unverified card statements.
URLs are only `DISCOVERED`: canonical HTTPS Bilibili video or Heybox article URLs,
with no credentials, query, fragment or alternate port. Queries on supported
content paths are discarded; Heybox's fixed share route extracts one valid
`link_id`. Duplicate IDs, unsupported hosts and short links are rejected, and raw
tracking or credential-like query values never enter the projection. Other URLs and unknown
fields are discarded. Neither a card summary nor its URL proves content was read.
The current card's supported URL can enter the existing Content Reader path when
that path is enabled and authorized. Its observation, not the card itself, proves
whether the link's content was read. Links inside quoted bodies do not trigger reads.

Quoted bodies prefer embedded text. Missing or placeholder-only text falls back
to `get_msg`; forwards expand `get_forward_msg.messages` in order, retaining
unverified display names and node paths. Bounds apply across the whole event:
4 requests, 5 seconds total wait, 3 nested reference levels, 32 projected text
nodes, 6,000 text characters, and the original 16 KiB rich JSON envelope. UTF-8
overflow truncates the body instead of rejecting the owner's question. Cycles,
unavailable originals, unsupported media and each reached limit have fixed issue
codes. `body.status` is `READ`, `PARTIAL`, `TRUNCATED`, `MISSING` or `UNSUPPORTED`;
placeholders alone never count as read text. If the envelope cannot hold even a
body status, the existing segment coverage continues to state missing/unsupported.

The signed capture evidence stores the redacted projection with the original
frame. Approval replays that exact projection and compares its layout/references
against the original frame; it does not query mutable source messages again.
Protocol responses and refreshed media URLs are not saved. The fixed SnowLuma
implementation may refresh media metadata internally during these read actions;
Bridge limits its own requests, response bytes and wait, not SnowLuma's internal
work. See [the pinned-source compatibility notes](p12-qq-media.md#引用与转发正文有界读取实现).

Bounds are 64 push segments, 16 KiB projected rich JSON, 16 KiB raw card JSON,
card depth 4 / 128 nodes, titles 128 characters and summaries 512 characters.
Duplicate JSON keys, non-finite card numbers, NUL text, malformed references and
over-limit values fail closed. Existing outer frame byte/depth/node limits and
the HTTP v1 128 KiB body cap still apply; HTTP inbox JSON also rejects duplicate
keys and depth/node excess before contract parsing. Bounded captures require the
approved owner/bot pair, fresh private/friend messages and ordinary owner text or
a validated references-only input under `references_v1`. In the latter case owner
text stays empty; a model display hint does not become owner or Memory evidence.
Empty plain messages and owner slash commands remain rejected.

For a rich input, `input_sha256` is SHA-256 of canonical UTF-8 JSON
`{"text": payload.text, "rich": payload.rich}` (sorted keys, no extra whitespace,
Unicode unescaped). Bridge and Core implement this independently. Plain inputs
keep the exact previous SHA-256 of canonical JSON `payload.text`; absent rich data
does not add a null field to legacy event fingerprints. Rich mutation fails the
existing approval/job/model/delivery checks. Selected history retains its separate
untrusted reference block, complete source hash and actual UTF-8 byte cost.

References are separate untrusted model context, never owner command text or native
memory evidence. Forgetting the source erases the whole event body, including rich
projection; replaying the latest Ledger against a pre-erasure copy removes it again.
Tests use public-shaped synthetic events, real authenticated localhost WebSockets,
HTTP v1 ASGI and mock model/send transports. No real QQ/media/provider acceptance
is claimed. Image acquisition remains a later independently authorized A2 scope.
