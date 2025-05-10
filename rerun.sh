uv clean && rm -rf ./build
rm -rf ./mcp.json
rm -rf ./mcp.lock
rm -rf ./mcp.lockb
rm -rf ./mcp.lockb.tmp
rm -rf ~/.operative/config.json

npx playwright uninstall --all
rm ~/.cursor/mcp.json

# uvx --from git+https://github.com/nandatheguntupalli/web-eval-agent.git webEvalAgent

#  OPERATIVE_API_KEY=<KEY> uv run webEvalAgent/mcp_server.py