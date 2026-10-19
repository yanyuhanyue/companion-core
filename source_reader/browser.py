"""One disposable Chromium context renders an already fetched public document offline."""

import asyncio
import os

from source_reader.network import ReadFailure, video_url


class BrowserRenderer:
    def __init__(self, timeout=5):
        self.timeout = timeout
        self.lock = asyncio.Lock()
        self.active_contexts = 0

    async def metadata(self, url, document):
        from playwright.async_api import Error, async_playwright
        from playwright.async_api import TimeoutError as BrowserTimeout

        video_url(url)
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
                    args=["--disable-background-networking", "--disable-extensions"],
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
                    await page.goto(url, wait_until="domcontentloaded")
                    result = await page.evaluate("""() => {
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
