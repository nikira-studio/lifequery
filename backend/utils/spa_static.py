"""Static-file serving with a safe single-page-app fallback."""

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class SPAStaticFiles(StaticFiles):
    """Serve the built UI and fall back to its index for browser routes.

    API routes are registered before this mount.  An unknown API or OpenAI
    endpoint must remain a real 404 rather than receiving the frontend HTML.
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if (
                exc.status_code != 404
                or scope["method"] not in {"GET", "HEAD"}
                or path.startswith(("api/", "v1/"))
            ):
                raise
            return await super().get_response("index.html", scope)
