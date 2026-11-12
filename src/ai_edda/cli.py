import ipaddress
import ssl
from pathlib import Path

import typer
import uvicorn
from cryptography import x509

from .access import OwnerAuth
from .api.app import config_root_default, create_app
from .project_store import _documents
from .tls import _validated_tls_names, create_tls_bundle

app = typer.Typer(no_args_is_help=True)


@app.command("download-model")
def download_model(config_root: Path = typer.Option(None)):
    """Download and verify the pinned analysis checkpoint."""
    from .model_download import ensure_model

    try:
        path, status = ensure_model(config_root or config_root_default())
    except (OSError, ValueError) as exc:
        typer.echo(
            f"Checkpoint download failed: {exc}. Check network access and disk space; remove an invalid checkpoint before retrying.",
            err=True,
        )
        raise typer.Exit(1) from exc
    typer.echo(f"Checkpoint {status}: {path}")


@app.command()
def doctor(
    project_root: Path = typer.Option(None),
    config_root: Path = typer.Option(None),
    device: str = typer.Option("cpu"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Inspect local setup and analysis readiness."""
    import json

    from .api.app import STATIC
    from .doctor import inspect

    if device not in ("cpu", "cuda"):
        raise typer.BadParameter("Choose cpu or cuda.", param_hint="--device")
    result = inspect(
        project_root or _documents() / "AI-EDDA",
        config_root or config_root_default(),
        STATIC,
        device,
    )
    if json_output:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
    else:
        typer.echo(f"Readiness: {'ready' if result['ok'] else 'not ready'}")
        typer.echo(f"Project: {result['paths']['project_root']}")
        typer.echo(f"Config: {result['paths']['config_root']}")
        typer.echo(f"Frontend: {'available' if result['frontend_present'] else 'missing'}")
        typer.echo(f"Owner config: {'available' if result['owner_config_present'] else 'missing'}")
        typer.echo(f"Model: {result['checkpoint']['status']}")
        cuda_available = result["hardware"]["cuda_available"]
        cuda_status = (
            "available"
            if cuda_available is True
            else "unavailable"
            if cuda_available is False
            else "not checked"
        )
        typer.echo(f"Requested device: {device}; CUDA: {cuda_status}")
        ram = result.get("available_ram_bytes")
        if ram is not None:
            typer.echo(f"Available RAM: {ram / (1024**3):.1f} GiB")
        for path, space in result.get("free_disk_bytes", {}).items():
            disk_status = f"{space / (1024**3):.1f} GiB" if space is not None else "unknown"
            typer.echo(f"Free disk ({path}): {disk_status}")
        for error in result.get("errors", []):
            typer.echo(f"Error: {error}")
        for warning in result.get("warnings", []):
            typer.echo(f"Warning: {warning}")
    if not result["ok"]:
        raise typer.Exit(1)


@app.callback()
def main():
    pass


@app.command()
def init(project_root: Path = typer.Option(None), config_root: Path = typer.Option(None)):
    """Initialize local storage and owner credentials."""
    project_root = project_root or _documents() / "AI-EDDA"
    config_root = config_root or config_root_default()
    project_root.mkdir(parents=True, exist_ok=True)
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "jobs").mkdir(exist_ok=True)
    owner = config_root / "owner.json"
    if not owner.exists():
        password = typer.prompt("Owner password", hide_input=True, confirmation_prompt=True)
        try:
            OwnerAuth.setup(owner, password)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    typer.echo("Initialization complete.")


@app.command()
def tls(name: list[str] = typer.Option(..., "--name"), config_root: Path = typer.Option(None)):
    """Create a local CA and server certificate."""
    root = config_root or config_root_default()
    root.mkdir(parents=True, exist_ok=True)
    try:
        bundle = create_tls_bundle(root / "tls", name)
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"CA certificate: {bundle.ca_certificate}\nCA SHA-256: {bundle.ca_sha256}")
    typer.echo(f"Server certificate: {bundle.server_certificate}\nServer key: {bundle.server_key}")
    typer.echo("Client trust must be configured manually.")


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    project_root: Path = typer.Option(None),
    config_root: Path = typer.Option(None),
    tls_cert: Path = typer.Option(None),
    tls_key: Path = typer.Option(None),
):
    """Start the local server."""
    config_root = config_root or config_root_default()
    if not (config_root / "owner.json").is_file():
        typer.echo("Owner configuration missing. Run ai-edda init first.", err=True)
        raise typer.Exit(1)
    if (tls_cert is None) != (tls_key is None):
        raise typer.BadParameter("Provide both TLS certificate and key.")
    if tls_cert is None:
        cert, key = config_root / "tls" / "server.pem", config_root / "tls" / "server-key.pem"
        if cert.exists() != key.exists():
            raise typer.BadParameter("The TLS bundle is incomplete.")
        if cert.exists():
            tls_cert, tls_key = cert, key
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback and not tls_cert:
        raise typer.BadParameter("Non-loopback binding requires TLS.")
    hosts = ["localhost", "127.0.0.1", "[::1]"]
    if tls_cert:
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(str(tls_cert), str(tls_key))
            certificate = x509.load_pem_x509_certificate(tls_cert.read_bytes())
            san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            dns_names = san.get_values_for_type(x509.DNSName)
            ip_names = san.get_values_for_type(x509.IPAddress)
            explicit_dns = [n for n in dns_names if "*" not in n]
            hosts = [n.lower() for n in explicit_dns]
            hosts += [f"[{n}]" if n.version == 6 else str(n) for n in ip_names]
            if not hosts:
                raise ValueError("Certificate has no explicit DNS or IP names.")
            _validated_tls_names(explicit_dns + [str(n) for n in ip_names])
        except (OSError, ValueError, ssl.SSLError, x509.ExtensionNotFound) as exc:
            raise typer.BadParameter(f"Invalid TLS certificate or key: {exc}") from exc
    for name in hosts:
        typer.echo(f"{'https' if tls_cert else 'http'}://{name}:{port}")
    uvicorn.run(
        create_app(project_root, config_root, allowed_hosts=hosts),
        host=host,
        port=port,
        workers=1,
        ssl_certfile=str(tls_cert) if tls_cert else None,
        ssl_keyfile=str(tls_key) if tls_key else None,
    )
