# QQ references, quotes and cards (A1)

`rich_input_scope="references_v1"` is an explicit, default-off input interpretation
scope. The bounded window pins it in `LiveTextWindowApproval`; changing that scope
changes the plan and the approved configuration. The persistent Bridge and Core
each have a separate `rich_input_scope` setting, defaulting to `none`. Enabling it
does not grant model, delivery, Content, media download or memory-write authority.
The original Spool, signed capture receipt, Budget, Outbox and Window Journal remain
the only input/accounting/delivery path. No extra OneBot HTTP query is made.

`payload.text` is the only current-owner text. In a segment array it concatenates
only `text.data.text`; a string is literal text, including CQ-looking syntax.
Optional `payload.rich` has `version="references_v1"`, `coverage="PARTIAL"` and an
ordered `segments` list. Each entry has the original zero-based `index`, `type`,
`actor="UNVERIFIED"` and a coverage code:

| Push segment | Retained projection | Coverage |
| --- | --- | --- |
| `reply` | Signed decimal message ID in `reference` | `MISSING`: original text unavailable |
| `forward` | Bounded opaque ASCII ID in `reference` | `UNFETCHED`: forward body not retrieved |
| `json` | Small title/summary/URL field projection with original JSON pointers | `PARTIAL`, or `UNSUPPORTED` |
| `node`, image/record/video/file, other | Type/index only; no body, sender claim or media URL | `UNSUPPORTED` |

The push-shape basis is SnowLuma commit
[`db142db9e6398c779283eab9afc3552da1e2c361`](https://github.com/SnowLuma/SnowLuma/tree/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/event-converter).
Its ordinary `to-message` path calls `elementsToOneBotSegments` without
`includeReplyContent`; the reply/forward codecs emit only `data.id`, and the JSON
codec emits an opaque `data.data` string. There is no ordinary node codec. This is
public-source evidence, not proof of a particular deployed image's source hash.

Card projection recognizes only explicit `/title`, `/desc`, `/summary`, `/url`,
`/jumpUrl`, and title/desc/URL fields below `/meta/detail_1` or `/meta/news`.
These are conservative extraction paths in opaque JSON, not a claim that every
SnowLuma card uses this schema. Fields remain unverified card statements.
URLs are only `DISCOVERED`: canonical HTTPS Bilibili video or Heybox article URLs,
with no credentials, query, fragment or alternate port. Other URLs and unknown
fields are discarded. Neither a card summary nor its URL proves content was read.
Reading a discovered URL requires the existing, separately authorized Content path.

Bounds are 64 push segments, 16 KiB projected rich JSON, 16 KiB raw card JSON,
card depth 4 / 128 nodes, titles 128 characters and summaries 512 characters.
Duplicate JSON keys, non-finite card numbers, NUL text, malformed references and
over-limit values fail closed. Existing outer frame byte/depth/node limits and
the HTTP v1 128 KiB body cap still apply; HTTP inbox JSON also rejects duplicate
keys and depth/node excess before contract parsing. Bounded captures still require
fresh ordinary owner text, the approved owner/bot pair and private/friend route.

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
