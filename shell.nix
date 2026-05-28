{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312;
in
pkgs.mkShell {
  buildInputs = [
    python
    python.pkgs.pip
    python.pkgs.virtualenv
    # nodejs_22 already bundles npm; pkgs.nodePackages.npm was removed in
    # newer nixpkgs and broke CI.
    pkgs.nodejs_22
    pkgs.ruff
  ];

  shellHook = ''
    # Set up Python virtualenv
    if [ ! -d .venv ]; then
      echo "Creating Python virtualenv..."
      ${python}/bin/python -m venv .venv
    fi
    source .venv/bin/activate

    # Install Python deps if pyproject.toml exists
    if [ -f pyproject.toml ]; then
      pip install -e ".[dev]" --quiet 2>/dev/null || true
    fi

    # Install frontend deps if package.json exists
    if [ -f frontend/package.json ] && [ ! -d frontend/node_modules ]; then
      echo "Installing frontend dependencies..."
      (cd frontend && npm install)
    fi

    echo "SkyNetControl dev environment ready."
  '';
}
