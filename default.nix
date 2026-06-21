{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312;
  frontend = import ./frontend.nix { inherit pkgs; };
  # Bake the running git SHA into the binary so the admin sidebar can
  # surface "this is commit X" — gives the operator a way to confirm
  # the deployed code matches what was pushed without shelling into the
  # box. Falls back to "unknown" when built from a non-git source (CI
  # tarball, archive, etc.); the runtime fallback to "dev" in
  # backend/version.py handles `run-dev.sh`.
  gitSha =
    if builtins.pathExists ./.git
    then pkgs.lib.substring 0 8 (pkgs.lib.commitIdFromGitRepo ./.git)
    else "unknown";
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
    prompt-toolkit
    pyyaml
    bleach
  ];

  postInstall = ''
    mkdir -p $out/share/skynetcontrol
    cp -r ${frontend} $out/share/skynetcontrol/static
    cp alembic.ini $out/share/skynetcontrol/
    cp -r alembic $out/share/skynetcontrol/alembic

    # alembic.ini's `script_location = alembic` is resolved relative to
    # CWD by default. Rewrite it to an absolute path so users can run
    # `skynetcontrol-alembic upgrade head` from any directory.
    substituteInPlace $out/share/skynetcontrol/alembic.ini \
      --replace "script_location = alembic" \
                "script_location = $out/share/skynetcontrol/alembic"

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

    # Database-copy CLI for cross-engine / host-to-host migrations.
    printf '%s\n' '#!${python}/bin/python' 'import sys' 'from backend.cli.db_copy import main' 'sys.exit(main())' > $out/bin/skynetcontrol-db-copy
    chmod +x $out/bin/skynetcontrol-db-copy

    # Recovery CLI for breaking back in after a misconfigured save.
    printf '%s\n' '#!${python}/bin/python' 'import sys' 'from backend.cli.recovery import main' 'sys.exit(main())' > $out/bin/skynetcontrol-recovery
    chmod +x $out/bin/skynetcontrol-recovery
  '';

  # Set SKYNET_STATIC_DIR on all wrapped programs. Also point ALEMBIC_CONFIG
  # at the bundled config so `skynetcontrol-alembic upgrade head` works
  # without the user needing to pass -c or cd into the share dir.
  makeWrapperArgs = [
    "--set" "SKYNET_STATIC_DIR" "${placeholder "out"}/share/skynetcontrol/static"
    "--set" "ALEMBIC_CONFIG" "${placeholder "out"}/share/skynetcontrol/alembic.ini"
    "--set" "SKYNET_GIT_SHA" gitSha
  ];

  meta = {
    description = "Winlink net management application";
    mainProgram = "skynetcontrol-server";
  };
}
