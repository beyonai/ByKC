"""Route registration for knowledge build APIs."""


def register_routes(  # pylint: disable=unused-argument
    app,
    *,
    get_document_chunking_service,
):
    """Register knowledge build API routes on the FastAPI app.

    All knowledge_build API routes have been deprecated and replaced
    by ``/api/v1/fileToMarkdownIndex`` in the knowledge_base module.
    This function is kept as a no-op so the dynamic module loader
    does not break.
    """
