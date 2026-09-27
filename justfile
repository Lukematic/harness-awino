# Contributor recipes. CI runs the same checks (.github/workflows/ci.yml).

# engine tests + extension tests
test:
    cd prototype && python3 -m unittest discover -s tests -t .
    cd integrations/vscode/extension && npm test

# lint the engine (zero findings is the bar)
lint:
    cd prototype && ruff check --exclude tests .

# compile the extension
build:
    cd integrations/vscode/extension && npm run compile
