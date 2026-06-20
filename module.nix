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

    secretsFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/run/agenix/skynetcontrol-secrets";
      description = ''
        Recommended for installs that manage secrets through agenix or
        sops-nix. Path to a single env-file-shaped secret containing
        SKYNET_JWT_SECRET_KEY (required) and optionally SKYNET_SECRETS_KEY,
        one per line:

        ```
        SKYNET_JWT_SECRET_KEY=<hex>
        SKYNET_SECRETS_KEY=<hex>
        ```

        Loaded via systemd EnvironmentFile. The file must be readable by
        root only; agenix/sops decrypt to a tmpfs path that already
        satisfies that.

        Mutually exclusive with jwtSecretFile / secretsKeyFile — set
        either secretsFile, OR the per-secret pair. The per-secret pair
        remains supported for installs that pre-date this option.
      '';
    };

    jwtSecretFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = ''
        Path to a file containing only the JWT signing secret. The file
        should be readable only by root; the unit reads it via systemd's
        LoadCredential mechanism, so the secret never appears in the Nix
        store. Generate with e.g. `openssl rand -hex 32 > /etc/skynetcontrol-jwt`.

        Prefer secretsFile for new deployments — it keeps both bootstrap
        secrets in a single agenix/sops file.
      '';
    };

    secretsKeyFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = ''
        Optional companion to jwtSecretFile: path to a file containing
        only the AES key material for at-rest encryption of OAuth client
        secrets and SMTP passwords in the AppConfig table. When unset,
        the JWT signing secret is reused — convenient, but it means
        rotating the JWT secret also invalidates every encrypted
        credential. Cannot be set together with secretsFile.
      '';
    };

    trustedProxies = lib.mkOption {
      type = lib.types.str;
      default = "";
      example = "127.0.0.1,::1";
      description = ''
        Comma-separated peer-IP allowlist for the per-IP rate limiter.
        When the connecting peer is in this list, the limiter trusts
        CF-Connecting-IP / X-Real-IP / X-Forwarded-For to identify the
        real client. Required behind a reverse proxy (nginx, Caddy,
        Cloudflare tunnel) or every visitor shares one bucket. Typical
        same-host proxy value: "127.0.0.1,::1".
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        # Exactly one of the two configuration shapes must be selected.
        # The XOR keeps "both null" and "both set" out of valid states.
        assertion =
          (cfg.secretsFile != null) != (cfg.jwtSecretFile != null);
        message =
          "services.skynetcontrol: set exactly one of `secretsFile` "
          + "(a single env-file with both bootstrap secrets) or "
          + "`jwtSecretFile` (legacy per-secret form). The legacy "
          + "form additionally accepts `secretsKeyFile` for the "
          + "at-rest encryption key.";
      }
      {
        assertion =
          !(cfg.secretsFile != null && cfg.secretsKeyFile != null);
        message =
          "services.skynetcontrol.secretsKeyFile cannot be combined "
          + "with secretsFile. Put SKYNET_SECRETS_KEY inside the "
          + "secretsFile env-file instead.";
      }
    ];

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
        SKYNET_STATE_DIR = cfg.stateDir;
        SKYNET_APP_BASE_URL = cfg.appBaseUrl;
      } // lib.optionalAttrs (cfg.trustedProxies != "") {
        SKYNET_TRUSTED_PROXIES = cfg.trustedProxies;
      };

      serviceConfig = {
        Type = "simple";
        User = "skynetcontrol";
        Group = "skynetcontrol";
        # skynetcontrol-alembic is a wrapped entry point created in default.nix
        # postInstall alongside skynetcontrol-server; wrapPythonPrograms sets the
        # correct PYTHONPATH so alembic can find the migrations package.
        ExecStartPre = "${skynetcontrol}/bin/skynetcontrol-alembic -c ${skynetcontrol}/share/skynetcontrol/alembic.ini upgrade head";
        WorkingDirectory = cfg.stateDir;
        Restart = "on-failure";
        RestartSec = 5;

        # Hardening
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ cfg.stateDir ];
      } // (
        if cfg.secretsFile != null then {
          # New shape: one env-file with everything. systemd reads it
          # natively at unit start; no shell wrapping needed.
          EnvironmentFile = [ cfg.secretsFile ];
          ExecStart =
            "${skynetcontrol}/bin/skynetcontrol-server "
            + "backend.app:create_app --factory "
            + "--host ${cfg.host} --port ${toString cfg.port}";
        } else {
          # Legacy shape: LoadCredential per secret, then a shell wrapper
          # reads each file and exports it before exec'ing the server.
          LoadCredential =
            [ "jwt:${cfg.jwtSecretFile}" ]
            ++ lib.optional (cfg.secretsKeyFile != null) "secrets:${cfg.secretsKeyFile}";
          ExecStart = ''
            ${pkgs.bash}/bin/bash -c '\
              export SKYNET_JWT_SECRET_KEY="$(cat $CREDENTIALS_DIRECTORY/jwt)" && \
              ${if cfg.secretsKeyFile != null
                then "export SKYNET_SECRETS_KEY=\"$(cat $CREDENTIALS_DIRECTORY/secrets)\" && "
                else ""} \
              exec ${skynetcontrol}/bin/skynetcontrol-server backend.app:create_app --factory --host ${cfg.host} --port ${toString cfg.port}'
          '';
        }
      );
    };
  };
}
