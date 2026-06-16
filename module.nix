{ config, lib, pkgs, ... }:

let
  cfg = config.services.skynetcontrol;
  skynetcontrol = import ./default.nix { inherit pkgs; };
in
{
  options.services.skynetcontrol = {
    enable = lib.mkEnableOption "SkyNetControl Winlink net management";

    port = lib.mkOption {
      type = lib.types.port;
      default = 8000;
      description = "Port to listen on.";
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address to bind to.";
    };

    stateDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/skynetcontrol";
      description = "Directory for database and runtime state.";
    };

    databaseUrl = lib.mkOption {
      type = lib.types.str;
      # `sqlite:///` + an absolute path (starting with `/`) yields SQLAlchemy's
      # four-slash absolute-path SQLite URL form.
      default = "sqlite:///${cfg.stateDir}/skynetcontrol.db";
      defaultText = lib.literalExpression ''"sqlite:///''${cfg.stateDir}/skynetcontrol.db"'';
      description = "SQLAlchemy database URL. Defaults to SQLite in stateDir.";
    };

    appBaseUrl = lib.mkOption {
      type = lib.types.str;
      example = "https://skynetcontrol.example.org";
      description = ''
        Externally-visible base URL of the running app. Used to construct
        OAuth provider redirect URIs and to compose links in transactional
        emails. Must match the host the user's browser hits (including
        scheme and any non-default port).
      '';
    };

    jwtSecretFile = lib.mkOption {
      type = lib.types.path;
      description = ''
        Path to a file containing the JWT signing secret on disk. The file
        should be readable only by root; the unit reads it via systemd's
        LoadCredential mechanism, so the secret never appears in the Nix
        store. Generate with e.g. `openssl rand -hex 32 > /etc/skynetcontrol-jwt`.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.skynetcontrol = {
      isSystemUser = true;
      group = "skynetcontrol";
      home = cfg.stateDir;
      description = "SkyNetControl service user";
    };
    users.groups.skynetcontrol = {};

    # Pre-create stateDir with correct ownership so the service can write to
    # it on first start, including when stateDir lives outside /var/lib (e.g.
    # /storage/skynetcontrol on a ZFS dataset).
    systemd.tmpfiles.rules = [
      "d ${cfg.stateDir} 0750 skynetcontrol skynetcontrol - -"
    ];

    systemd.services.skynetcontrol = {
      description = "SkyNetControl Winlink Net Management";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];

      environment = {
        SKYNET_DATABASE_URL = cfg.databaseUrl;
        SKYNET_APP_BASE_URL = cfg.appBaseUrl;
      };

      serviceConfig = {
        Type = "simple";
        User = "skynetcontrol";
        Group = "skynetcontrol";
        # skynetcontrol-alembic is a wrapped entry point created in default.nix
        # postInstall alongside skynetcontrol-server; wrapPythonPrograms sets the
        # correct PYTHONPATH so alembic can find the migrations package.
        ExecStartPre = "${skynetcontrol}/bin/skynetcontrol-alembic -c ${skynetcontrol}/share/skynetcontrol/alembic.ini upgrade head";
        # Pipe the JWT secret through LoadCredential so it never lands in the
        # Nix store. The systemd-managed credential file is referenced as
        # $CREDENTIALS_DIRECTORY/jwt inside the unit; ExecStart wraps the
        # server in a small shell that reads it.
        LoadCredential = [ "jwt:${cfg.jwtSecretFile}" ];
        ExecStart = ''
          ${pkgs.bash}/bin/bash -c '\
            export SKYNET_JWT_SECRET_KEY="$(cat $CREDENTIALS_DIRECTORY/jwt)" && \
            exec ${skynetcontrol}/bin/skynetcontrol-server backend.app:create_app --factory --host ${cfg.host} --port ${toString cfg.port}'
        '';
        WorkingDirectory = cfg.stateDir;
        Restart = "on-failure";
        RestartSec = 5;

        # Hardening
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ cfg.stateDir ];
      };
    };
  };
}
