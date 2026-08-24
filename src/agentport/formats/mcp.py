import re

from .. import jsonc, minitoml
from ..errors import FormatError, UsageError
from ..ir import McpDocument, ServerDef
from ..safety import mask_mapping

MCP_FAMILIES = ["claude", "cursor", "vscode", "opencode", "codex", "windsurf", "gemini"]

GLOBAL_TARGET_HINTS = {
    "claude": [
        "Windows: %APPDATA%\\Claude\\claude_desktop_config.json",
        "macOS: ~/Library/Application Support/Claude/claude_desktop_config.json",
        "Linux: ~/.config/Claude/claude_desktop_config.json",
    ],
    "codex": ["all OSes: ~/.codex/config.toml"],
    "windsurf": ["all OSes: ~/.codeium/windsurf/mcp_config.json"],
    "gemini": ["all OSes: ~/.gemini/settings.json"],
}

PROJECT_DEFAULT_PATHS = {
    "cursor": ".cursor/mcp.json",
    "vscode": ".vscode/mcp.json",
    "opencode": "opencode.json",
}

SERVER_KEY_BY_FAMILY = {
    "claude": "mcpServers",
    "cursor": "mcpServers",
    "windsurf": "mcpServers",
    "gemini": "mcpServers",
    "vscode": "servers",
    "opencode": "mcp",
}

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def validate_server_name(name):
    if not _NAME_RE.match(str(name)):
        raise FormatError(
            f"invalid MCP server name: {name!r}",
            hint="names must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}",
        )
    return str(name)


def _clean_env(raw, warnings, ctx):
    env = {}
    if raw is None:
        return env
    if not isinstance(raw, dict):
        warnings.append(f"WARN {ctx}: env is not an object; dropped")
        return env
    for k, v in list(raw.items())[:100]:
        if isinstance(v, dict) or isinstance(v, list):
            warnings.append(f"WARN {ctx}: non-scalar env value for {k}; dropped")
            continue
        env[str(k)] = str(v)
    return env


def _clean_str_list(raw, warnings, ctx):
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        warnings.append(f"WARN {ctx}: args is not a list; dropped")
        return []
    out = []
    for item in raw[:200]:
        if isinstance(item, (str, int, float)):
            out.append(str(item))
        else:
            warnings.append(f"WARN {ctx}: non-scalar arg dropped")
    return out


def _server_from_stdio(name, command, args, env, warnings, ctx, extras=None):
    if not isinstance(command, str) or not command.strip():
        warnings.append(f"WARN {ctx}: missing/empty command; server skipped")
        return None
    return ServerDef(
        name=validate_server_name(name),
        transport="stdio",
        command=command.strip(),
        args=_clean_str_list(args, warnings, ctx),
        env=_clean_env(env, warnings, ctx),
        extras=dict(extras or {}),
    )


def _server_from_remote(name, url, headers, warnings, ctx, transport="http", extras=None):
    if not isinstance(url, str) or not url.strip():
        warnings.append(f"WARN {ctx}: missing/empty url; server skipped")
        return None
    url = url.strip()
    if not _URL_SCHEME_RE.match(url):
        scheme = url.split(":", 1)[0] if ":" in url else "?"
        warnings.append(
            f"WARN {ctx}: url scheme is not http(s): '{scheme}' (kept as-is)"
        )
    return ServerDef(
        name=validate_server_name(name),
        transport=transport,
        url=url,
        headers=_clean_env(headers, warnings, ctx),
        extras=dict(extras or {}),
    )


def _split_extras(mapping, known_keys):
    return {k: v for k, v in mapping.items() if k not in known_keys}


def parse_family_json(obj, family, warnings):
    doc = McpDocument()
    if not isinstance(obj, dict):
        raise FormatError(
            f"{family}: config root must be a JSON object, "
            f"got {type(obj).__name__}"
        )
    key = SERVER_KEY_BY_FAMILY[family]
    servers_raw = obj.get(key)
    if servers_raw is None:
        warnings.append(f"WARN no '{key}' section found; treating as empty")
        servers_raw = {}
    if not isinstance(servers_raw, dict):
        raise FormatError(f"'{key}' must be an object mapping names to server configs")
    doc.extras = {k: v for k, v in obj.items() if k != key}
    for name, cfg in servers_raw.items():
        ctx = f"{family}:{name}"
        validate_server_name(name)
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            warnings.append(f"WARN {ctx}: config is not an object; skipped")
            continue
        disabled = bool(cfg.get("disabled", False))
        known = {"command", "args", "env", "url", "headers", "disabled"}
        extras = _split_extras(cfg, known)
        if "command" in cfg:
            srv = _server_from_stdio(
                name, cfg.get("command"), cfg.get("args"), cfg.get("env"),
                warnings, ctx, extras=extras,
            )
        elif "url" in cfg:
            srv = _server_from_remote(
                name, cfg.get("url"), cfg.get("headers"),
                warnings, ctx, extras=extras,
            )
        else:
            warnings.append(f"WARN {ctx}: neither command nor url found; skipped")
            continue
        if srv is not None:
            srv.disabled = disabled
            doc.servers.append(srv)
    return doc


def parse_vscode(obj, warnings):
    doc = McpDocument()
    if not isinstance(obj, dict):
        raise FormatError(
            f"vscode: config root must be a JSON object, "
            f"got {type(obj).__name__}"
        )
    servers_raw = obj.get("servers")
    if servers_raw is None:
        warnings.append("WARN vscode: no 'servers' section found; treating as empty")
        servers_raw = {}
    if not isinstance(servers_raw, dict):
        raise FormatError("'servers' must be an object mapping names to server configs")
    doc.extras = {k: v for k, v in obj.items() if k != "servers"}
    for name, cfg in servers_raw.items():
        ctx = f"vscode:{name}"
        validate_server_name(name)
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            warnings.append(f"WARN {ctx}: config is not an object; skipped")
            continue
        stype = cfg.get("type")
        known = {"command", "args", "env", "url", "headers", "type"}
        extras = _split_extras(cfg, known)
        if stype in ("http", "sse") or ("url" in cfg and "command" not in cfg):
            transport = "sse" if stype == "sse" else "http"
            srv = _server_from_remote(
                name, cfg.get("url"), cfg.get("headers"), warnings, ctx,
                transport=transport, extras=extras,
            )
        elif "command" in cfg or stype == "stdio":
            srv = _server_from_stdio(
                name, cfg.get("command"), cfg.get("args"), cfg.get("env"),
                warnings, ctx, extras=extras,
            )
        else:
            warnings.append(f"WARN {ctx}: cannot determine transport; skipped")
            srv = None
        if srv is not None:
            # Honor a well-known 'disabled' extra so round-trips rehydrate
            # the flag even though VS Code has no native disable concept.
            if cfg.get("disabled") is True:
                srv.disabled = True
            doc.servers.append(srv)
    return doc


def parse_opencode(obj, warnings):
    doc = McpDocument()
    if not isinstance(obj, dict):
        raise FormatError(
            f"opencode: config root must be a JSON object, "
            f"got {type(obj).__name__}"
        )
    servers_raw = obj.get("mcp")
    if servers_raw is None:
        warnings.append("WARN opencode: no 'mcp' section found; treating as empty")
        servers_raw = {}
    if not isinstance(servers_raw, dict):
        raise FormatError("'mcp' must be an object mapping names to server configs")
    doc.extras = {k: v for k, v in obj.items() if k != "mcp"}
    for name, cfg in servers_raw.items():
        ctx = f"opencode:{name}"
        validate_server_name(name)
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            warnings.append(f"WARN {ctx}: config is not an object; skipped")
            continue
        otype = cfg.get("type", "local")
        enabled = cfg.get("enabled", True)
        known = {"type", "command", "environment", "env", "url", "headers", "enabled"}
        extras = _split_extras(cfg, known)
        if otype == "local":
            cmd_list = cfg.get("command")
            if isinstance(cmd_list, str):
                cmd_list = [cmd_list]
            if not isinstance(cmd_list, list) or len(cmd_list) < 1:
                warnings.append(f"WARN {ctx}: local server requires a command array; skipped")
                continue
            command = str(cmd_list[0])
            args = [str(a) for a in cmd_list[1:]]
            srv = _server_from_stdio(
                name, command, args,
                cfg.get("environment", cfg.get("env")),
                warnings, ctx, extras=extras,
            )
        elif otype == "remote":
            srv = _server_from_remote(
                name, cfg.get("url"), cfg.get("headers"), warnings, ctx, extras=extras,
            )
        else:
            warnings.append(f"WARN {ctx}: unknown type {otype!r}; skipped")
            continue
        if srv is not None:
            srv.disabled = (enabled is False)
            doc.servers.append(srv)
    return doc


def parse_codex(obj, warnings):
    doc = McpDocument()
    if not isinstance(obj, dict):
        raise FormatError(
            f"codex: config root must be a table, got {type(obj).__name__}"
        )
    doc.extras = {k: v for k, v in obj.items() if k != "mcp_servers"}
    servers_raw = obj.get("mcp_servers", {})
    if not isinstance(servers_raw, dict):
        raise FormatError("'[mcp_servers]' must be a table of tables")
    for name, cfg in servers_raw.items():
        ctx = f"codex:{name}"
        validate_server_name(name)
        if not isinstance(cfg, dict):
            warnings.append(f"WARN {ctx}: config is not a table; skipped")
            continue
        known = {"command", "args", "env", "url"}
        extras = _split_extras(cfg, known)
        if "command" in cfg:
            srv = _server_from_stdio(
                name, cfg.get("command"), cfg.get("args"), cfg.get("env"),
                warnings, ctx, extras=extras,
            )
        elif "url" in cfg:
            warnings.append(f"WARN {ctx}: streamable HTTP requires a recent Codex build")
            srv = _server_from_remote(name, cfg.get("url"), {}, warnings, ctx, extras=extras)
        else:
            warnings.append(f"WARN {ctx}: neither command nor url found; skipped")
            continue
        if srv is not None:
            doc.servers.append(srv)
    return doc


def sniff_family(path, text):
    from pathlib import PurePath

    p = PurePath(str(path)).name.lower()
    if p.endswith(".toml") or "config.toml" in p:
        return "codex"
    if "opencode" in p:
        return "opencode"
    if "windsurf" in p or "codeium" in p:
        return "windsurf"
    if "gemini" in p:
        return "gemini"
    if "claude" in p and p.endswith((".json", ".jsonc")):
        return "claude"
    try_probe = strip_for_sniff(text)
    has_mcp_lower = '"mcp"' in try_probe
    has_mcp_camel = '"mcpServers"' in try_probe.lower() or '"mcpservers"' in try_probe.lower()
    if has_mcp_camel:
        if "claude" in p:
            return "claude"
        return "cursor"
    if has_mcp_lower:
        return "opencode"
    if '"servers"' in try_probe:
        return "vscode"
    return None


def strip_for_sniff(text):
    try:
        return jsonc.strip_jsonc(text)
    except FormatError:
        return text


def parse_source_text(text, family, warnings):
    if family == "codex":
        parsed = minitoml.parse(text)
        return parse_codex(parsed, warnings), parsed
    obj = jsonc.loads_jsonc(text)
    if family == "vscode":
        return parse_vscode(obj, warnings), obj
    if family == "opencode":
        return parse_opencode(obj, warnings), obj
    return parse_family_json(obj, family, warnings), obj


def merge_documents(base_doc, incoming_doc, conflict_policy, prune, warnings):
    result = {}
    for s in base_doc.servers:
        result[s.name] = s
    incoming_names = {s.name for s in incoming_doc.servers}
    for s in incoming_doc.servers:
        existing = result.get(s.name)
        if existing is not None:
            if existing.identity() == s.identity():
                continue
            if conflict_policy == "overwrite":
                result[s.name] = s
            else:
                warnings.append(
                    f"WARN conflict on server '{s.name}': keeping existing "
                    "(use --conflict overwrite to prefer incoming)"
                )
        else:
            result[s.name] = s
    if prune:
        for name in sorted(set(result.keys()) - incoming_names):
            warnings.append(f"WARN pruned server absent from source: {name}")
            del result[name]
    merged = McpDocument()
    merged.servers = [result[n] for n in sorted(result.keys())]
    merged.extras = dict(base_doc.extras)
    return merged


def render_server_for_json_families(srv, family, warnings):
    entry = {}
    if srv.transport == "stdio":
        entry["command"] = srv.command
        if srv.args:
            entry["args"] = list(srv.args)
        if srv.env:
            entry["env"] = dict(srv.env)
    else:
        entry["url"] = srv.url
        if srv.headers:
            entry["headers"] = dict(srv.headers)
    if srv.disabled:
        if family in ("claude", "windsurf", "gemini"):
            warnings.append(
                f"WARN {family} does not support disabling servers; "
                f"'{srv.name}' emitted fully enabled (was disabled in source)"
            )
        elif family == "cursor":
            entry["disabled"] = True
        elif family == "vscode":
            # VS Code has no native disable flag; keep the state losslessly
            # as a well-known extra so round-trips preserve it.
            entry.setdefault("disabled", True)
    for k, v in srv.extras.items():
        entry.setdefault(k, v)
    return entry


def render_family_json(doc, base_obj, family, replace, conflict_policy, warnings):
    key = SERVER_KEY_BY_FAMILY[family]
    out_obj = {} if replace else dict(base_obj or {})
    existing_section = out_obj.get(key)
    merged = {}
    conflicts = []
    if isinstance(existing_section, dict) and not replace:
        for name, cfg in existing_section.items():
            merged[name] = cfg
    for srv in doc.sorted_servers():
        rendered = render_server_for_json_families(srv, family, warnings)
        if srv.name in merged:
            if conflict_policy == "overwrite":
                merged[srv.name] = rendered
            else:
                conflicts.append(srv.name)
        else:
            merged[srv.name] = rendered
    if conflicts:
        warnings.append(
            f"WARN kept existing config for {len(conflicts)} conflicting server(s): "
            + ", ".join(sorted(conflicts))
            + " (use --conflict overwrite to prefer the source)"
        )
    out_obj[key] = merged
    return jsonc.dumps_pretty(out_obj), out_obj


def render_vscode(doc, base_obj, replace, conflict_policy, warnings):
    out_obj = {} if replace else dict(base_obj or {})
    existing = out_obj.get("servers")
    merged = {}
    conflicts = []
    if isinstance(existing, dict) and not replace:
        for name, cfg in existing.items():
            merged[name] = cfg
    for srv in doc.sorted_servers():
        rendered = {}
        if srv.transport == "stdio":
            rendered["command"] = srv.command
            if srv.args:
                rendered["args"] = list(srv.args)
            if srv.env:
                rendered["env"] = dict(srv.env)
        else:
            rendered["type"] = srv.transport
            rendered["url"] = srv.url
            if srv.headers:
                rendered["headers"] = dict(srv.headers)
        if srv.disabled:
            # VS Code has no native disable flag; keep state losslessly.
            rendered.setdefault("disabled", True)
        for k, v in srv.extras.items():
            rendered.setdefault(k, v)
        if srv.name in merged:
            if conflict_policy == "overwrite":
                merged[srv.name] = rendered
            else:
                conflicts.append(srv.name)
        else:
            merged[srv.name] = rendered
    if conflicts:
        warnings.append(
            "WARN kept existing config for conflicting server(s): " + ", ".join(sorted(conflicts))
        )
    out_obj["servers"] = merged
    return jsonc.dumps_pretty(out_obj), out_obj


def render_opencode(doc, base_obj, replace, conflict_policy, warnings):
    out_obj = {} if replace else dict(base_obj or {})
    existing = out_obj.get("mcp")
    merged = {}
    conflicts = []
    if isinstance(existing, dict) and not replace:
        for name, cfg in existing.items():
            merged[name] = cfg
    for srv in doc.sorted_servers():
        if srv.transport == "stdio":
            entry = {"type": "local", "command": [srv.command] + list(srv.args)}
            if srv.env:
                entry["environment"] = dict(srv.env)
        else:
            entry = {"type": "remote", "url": srv.url}
            if srv.headers:
                entry["headers"] = dict(srv.headers)
        if srv.disabled:
            entry["enabled"] = False
        for k, v in srv.extras.items():
            entry.setdefault(k, v)
        if srv.name in merged:
            if conflict_policy == "overwrite":
                merged[srv.name] = entry
            else:
                conflicts.append(srv.name)
        else:
            merged[srv.name] = entry
    if conflicts:
        warnings.append(
            "WARN kept existing config for conflicting server(s): " + ", ".join(sorted(conflicts))
        )
    out_obj["mcp"] = merged
    return jsonc.dumps_pretty(out_obj), out_obj


def render_codex(doc, base_obj, replace, conflict_policy, had_comments, warnings):
    out_obj = {} if replace else dict(base_obj or {})
    existing = out_obj.get("mcp_servers")
    merged = {}
    conflicts = []
    if isinstance(existing, dict) and not replace:
        for name, cfg in existing.items():
            merged[name] = cfg
    for srv in doc.sorted_servers():
        entry = {}
        if srv.transport == "stdio":
            entry["command"] = srv.command
            if srv.args:
                entry["args"] = list(srv.args)
            if srv.env:
                entry["env"] = dict(srv.env)
        else:
            entry["url"] = srv.url
        if srv.disabled:
            warnings.append(
                f"WARN codex has no 'disabled' concept; server '{srv.name}' emitted fully enabled"
            )
        for k, v in srv.extras.items():
            entry[k] = v
        if srv.name in merged:
            if conflict_policy == "overwrite":
                merged[srv.name] = entry
            else:
                conflicts.append(srv.name)
        else:
            merged[srv.name] = entry
    if conflicts:
        warnings.append(
            "WARN kept existing config for conflicting server(s): " + ", ".join(sorted(conflicts))
        )
    if had_comments:
        warnings.append("WARN existing TOML comments were dropped during merge")
    out_obj["mcp_servers"] = merged
    return minitoml.dumps(out_obj), out_obj


def document_to_masked_preview(doc, family):
    servers = {}
    for srv in doc.sorted_servers():
        entry = {}
        if srv.transport == "stdio":
            entry["transport"] = "stdio"
            entry["command"] = srv.command
            if srv.args:
                entry["args"] = list(srv.args)
            if srv.env:
                entry["env"] = mask_mapping(srv.env)
        else:
            entry["transport"] = srv.transport
            entry["url"] = srv.url
            if srv.headers:
                entry["headers"] = mask_mapping(srv.headers)
        if srv.disabled:
            entry["disabled"] = True
        servers[srv.name] = entry
    return {"family": family, "servers": servers}


def validate_target(target_key):
    if target_key not in MCP_FAMILIES:
        known = ", ".join(MCP_FAMILIES)
        raise UsageError(f"unknown mcp target family: {target_key}", hint=f"known families: {known}")
