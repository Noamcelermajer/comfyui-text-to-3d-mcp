# Starts the MCP server so directories (e.g. Glama) can list its tools.
# Generating assets needs a local ComfyUI and an NVIDIA GPU, which this image does not
# include; the server starts and answers tools/list without them.
FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["python", "forge.py", "mcp"]
