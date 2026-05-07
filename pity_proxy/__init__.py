import urllib.parse

from pity_proxy.mock import PityMock
from pity_proxy.record import PityRecorder
from config import Config


async def start_proxy(log):
    """
    start mitmproxy server
    :return:
    """
    try:
        import werkzeug.urls
        if not hasattr(werkzeug.urls, "url_quote"):
            # Flask 2.0.x still imports url_quote, but newer Werkzeug removed it.
            werkzeug.urls.url_quote = urllib.parse.quote
        from mitmproxy import options
        from mitmproxy.tools.dump import DumpMaster
    except ImportError:
        log.bind(name=None).warning(
            "mitmproxy not installed, Please see: https://docs.mitmproxy.org/stable/overview-installation/")
        return

    addons = [
        PityRecorder()
    ]
    try:
        if Config.MOCK_ON:
            addons.append(PityMock())
        opts = options.Options(listen_host='0.0.0.0', listen_port=Config.PROXY_PORT)
        m = DumpMaster(opts, False, False)
        # remove global block
        block_addon = m.addons.get("block")
        if block_addon is not None:
            m.addons.remove(block_addon)
        # mitmproxy's errorcheck addon will sys.exit(1) on startup/runtime errors,
        # which should not bring down the main Argus service.
        errorcheck_addon = m.addons.get("errorcheck")
        if errorcheck_addon is not None:
            m.addons.remove(errorcheck_addon)
        m.addons.add(*addons)
        log.bind(name=None).debug(f"mock server is running at http://0.0.0.0:{Config.PROXY_PORT}")
        await m.run()
    except BaseException as e:
        log.bind(name=None).error(f"mock server running failed, please check: {e}")
