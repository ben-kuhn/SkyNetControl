{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312;
  frontend = import ./frontend.nix { inherit pkgs; };
in
python.pkgs.buildPythonApplication {
  pname = "skynetcontrol";
  version = "0.1.0";
  src = ./.;
  pyproject = true;

  build-system = [
    python.pkgs.setuptools
    python.pkgs.setuptools-scm
  ];

  dependencies = with python.pkgs; [
    fastapi
    uvicorn
    sqlalchemy
    alembic
    pydantic
    pydantic-settings
    authlib
    python-jose
    httpx
    anthropic
    jinja2
  ];

  postInstall = ''
    mkdir -p $out/share/skynetcontrol
    cp -r ${frontend} $out/share/skynetcontrol/static
    cp alembic.ini $out/share/skynetcontrol/
    cp -r alembic $out/share/skynetcontrol/alembic

    # Create a Python entry point script that invokes uvicorn.
    # buildPythonApplication's wrapPythonPrograms hook will wrap this
    # with the correct PYTHONPATH containing all dependencies.
    mkdir -p $out/bin
    printf '%s\n' '#!${python}/bin/python' 'import sys' 'from uvicorn.main import main' 'sys.exit(main())' > $out/bin/skynetcontrol-server
    chmod +x $out/bin/skynetcontrol-server

    # Create an alembic entry point so the NixOS module can run migrations.
    # wrapPythonPrograms will wrap this with the correct PYTHONPATH.
    printf '%s\n' '#!${python}/bin/python' 'import sys' 'from alembic.config import main' 'sys.exit(main())' > $out/bin/skynetcontrol-alembic
    chmod +x $out/bin/skynetcontrol-alembic
  '';

  # Set SKYNET_STATIC_DIR on all wrapped programs
  makeWrapperArgs = [
    "--set" "SKYNET_STATIC_DIR" "${placeholder "out"}/share/skynetcontrol/static"
  ];

  meta = {
    description = "Winlink net management application";
    mainProgram = "skynetcontrol-server";
  };
}
