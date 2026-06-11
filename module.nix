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

    settings = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = {};
      description = "Additional environment variables (SKYNET_ prefix added automatically if missing).";
      example = {
        DEBUG = "true";
      };
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
      } // lib.mapAttrs' (name: value:
        let
          envName = if lib.hasPrefix "SKYNET_" name then name else "SKYNET_${name}";
        in
        lib.nameValuePair envName value
      ) cfg.settings;

      serviceConfig = {
        Type = "simple";
        User = "skynetcontrol";
        Group = "skynetcontrol";
        # skynetcontrol-alembic is a wrapped entry point created in default.nix
        # postInstall alongside skynetcontrol-server; wrapPythonPrograms sets the
        # correct PYTHONPATH so alembic can find the migrations package.
        ExecStartPre = "${skynetcontrol}/bin/skynetcontrol-alembic -c ${skynetcontrol}/share/skynetcontrol/alembic.ini upgrade head";
        ExecStart = "${skynetcontrol}/bin/skynetcontrol-server backend.app:create_app --factory --host ${cfg.host} --port ${toString cfg.port}";
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
