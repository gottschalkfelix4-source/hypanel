import os

if os.getenv("HYPANEL_MODE") in {"runner", "install"}:
    from .runner import install, run
    if os.getenv("HYPANEL_MODE") == "install":
        install()
    else:
        raise SystemExit(run())
else:
    import uvicorn
    uvicorn.run("hypanel.app:app", host="0.0.0.0", port=8080, workers=1, proxy_headers=True,
                forwarded_allow_ips=os.getenv("HYPANEL_TRUSTED_PROXIES", "127.0.0.1"))
