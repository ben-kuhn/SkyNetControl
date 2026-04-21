{ pkgs ? import <nixpkgs> {} }:

pkgs.runCommand "skynetcontrol-frontend-stub" {} ''
  mkdir -p $out
  cat > $out/index.html <<'HTML'
<!DOCTYPE html>
<html>
<head><title>SkyNetControl</title></head>
<body><h1>SkyNetControl</h1><p>Frontend not yet built.</p></body>
</html>
HTML
''
