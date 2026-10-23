"""One disposable Chromium context renders an already fetched public document offline."""

import asyncio
import os

from source_reader.network import ReadFailure, video_url

# HTTP interception/offline/CSP do not revoke WebRTC's ICE sockets. Install this in
# every document before page scripts, including initial about:blank child realms.
_DISABLE_RTC = """(() => {
    const deny = function() {
        throw new DOMException('Realtime networking is disabled in the Reader', 'SecurityError');
    };
    const names = new Set(['RTCPeerConnection', 'webkitRTCPeerConnection',
        ...Object.getOwnPropertyNames(globalThis).filter(name => name.startsWith('RTC'))]);
    for (const name of names) {
        const ctor = globalThis[name];
        if (typeof ctor === 'function' && ctor.prototype) {
            for (const key of Reflect.ownKeys(ctor.prototype)) {
                const property = Object.getOwnPropertyDescriptor(ctor.prototype, key);
                if (typeof property.value === 'function') {
                    Object.defineProperty(ctor.prototype, key,
                        {value:deny, writable:false, configurable:false});
                }
            }
            Object.freeze(ctor.prototype);
        }
        Object.defineProperty(globalThis, name,
            {value:undefined, writable:false, configurable:false});
    }
})();"""


class BrowserRenderer:
    def __init__(self, timeout=5):
        self.timeout = timeout
        self.lock = asyncio.Lock()
        self.active_contexts = 0

    async def metadata(self, url, document):
        video_url(url)
        return await self._render(url, document, """() => {
            const d = window.__INITIAL_STATE__?.videoData;
            if (d && typeof d === 'object') return {
                title: String(d.title || '').slice(0, 300),
                desc: String(d.desc || '').slice(0, 1800), cid: d.cid,
                owner: {name: String(d.owner?.name || '').slice(0, 100)}
            };
            const meta = name => document.querySelector(`meta[property='${name}']`)
                ?.content || '';
            return {title: meta('og:title').slice(0,300),
                desc: meta('og:description').slice(0,1800)};
        }""")

    async def html(self, url, document):
        # The caller has already fetched this document through its host/DNS policy.
        # Rendering cannot fetch subresources or navigate to another document.
        from urllib.parse import urlsplit

        from source_reader.web import web_url

        web_url(url, {urlsplit(url).hostname})
        result = await self._render(url, document, """() => {
            const html = document.documentElement.outerHTML;
            return html.length <= 1048576 ? html : null;
        }""")
        if result is None or len(result.encode("utf-8")) > 1048576:
            raise ReadFailure("READER_BYTE_LIMIT")
        return result.encode("utf-8")

    async def _render(self, url, document, expression):
        from playwright.async_api import Error, async_playwright
        from playwright.async_api import TimeoutError as BrowserTimeout

        async with self.lock, async_playwright() as playwright:
            # Browser children receive no Core, provider, SSH or account environment values.
            env = {
                key: os.environ[key]
                for key in (
                    "PATH",
                    "SYSTEMROOT",
                    "WINDIR",
                    "TEMP",
                    "TMP",
                    "TMPDIR",
                    "LANG",
                    "DISPLAY",
                )
                if key in os.environ
            }
            try:
                browser = await playwright.chromium.launch(
                    headless=True,
                    chromium_sandbox=True,
                    env=env,
                    args=[
                        "--disable-background-networking", "--disable-extensions",
                        # Chrome and headless-shell read different switches into their
                        # renderer preferences. The JS guard also prevents TCP/relay RTC.
                        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                        "--block-new-web-contents",
                    ],
                )
            except Error as error:
                raise ReadFailure("READER_BROWSER_UNAVAILABLE") from error
            context = None
            try:
                async with asyncio.timeout(self.timeout):
                    context = await browser.new_context(
                        accept_downloads=False,
                        service_workers="block",
                        offline=True,
                        permissions=[],
                    )
                    self.active_contexts += 1
                    context.set_default_timeout(self.timeout * 1000)
                    await context.add_init_script(script=_DISABLE_RTC)
                    context.on(
                        "page",
                        lambda page: (
                            asyncio.create_task(page.close()) if len(context.pages) > 1 else None
                        ),
                    )
                    await context.route_web_socket("**/*", lambda route: route.close())
                    served = False

                    async def route(request):
                        nonlocal served
                        if (
                            not served
                            and request.request.url == url
                            and request.request.method == "GET"
                        ):
                            served = True
                            await request.fulfill(
                                status=200,
                                body=document,
                                headers={
                                    "Content-Type": "text/html; charset=utf-8",
                                    "Content-Security-Policy": "default-src 'none'; "
                                    "script-src 'unsafe-inline'; "
                                    "connect-src 'none'; frame-src 'none'; object-src 'none'; "
                                    "form-action 'none'; base-uri 'none'",
                                },
                            )
                        else:
                            await request.abort()

                    await context.route("**/*", route)
                    page = await context.new_page()
                    # A real document navigation runs the context initializer before
                    # inline scripts. set_content/document.write is not a navigation.
                    await page.goto(url, wait_until="domcontentloaded")
                    result = await page.evaluate(expression)
                    return result
            except (TimeoutError, BrowserTimeout) as error:
                raise ReadFailure("READER_BROWSER_TIMEOUT") from error
            except Error as error:
                raise ReadFailure("READER_BROWSER_FAILED") from error
            finally:
                try:
                    if context:
                        await context.close()
                finally:
                    self.active_contexts = 0
                    await browser.close()
