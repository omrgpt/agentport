from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ServerDef:
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    url: Optional[str] = None
    headers: dict = field(default_factory=dict)
    disabled: bool = False
    extras: dict = field(default_factory=dict)

    def identity(self):
        env_items = tuple(sorted((str(k), str(v)) for k, v in (self.env or {}).items()))
        header_items = tuple(sorted((str(k), str(v)) for k, v in (self.headers or {}).items()))
        arg_items = tuple(str(a) for a in (self.args or []))
        extras_items = tuple(sorted((k, repr(v)) for k, v in (self.extras or {}).items()))
        return (
            self.transport,
            self.command,
            arg_items,
            env_items,
            self.url,
            header_items,
            bool(self.disabled),
            extras_items,
        )


@dataclass
class McpDocument:
    servers: list = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    def by_name(self):
        return {s.name: s for s in self.servers}

    def sorted_servers(self):
        return sorted(self.servers, key=lambda s: s.name)


@dataclass
class InstructionDoc:
    body: str
    meta: Optional[dict] = None
    source_path: Optional[str] = None


@dataclass
class SkillDoc:
    name: str
    description: str
    license: Optional[str] = None
    allowed_tools: Optional[list] = None
    compatibility: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    body: str = ""
    source_dir: Optional[str] = None
    unknown_fields: dict = field(default_factory=dict)
