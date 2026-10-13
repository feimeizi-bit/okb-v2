"""Read-only declared configuration capture. Never queries or starts an Agent."""
import hashlib
import inspect
import json
import math
from pathlib import Path


def digest(data):return hashlib.sha256(data).hexdigest()


def strict_object(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise ValueError('duplicate config key')
        result[k]=v
    return result


def decode(data, kind):
    text=data.decode('utf-8-sig')
    if kind=='json':
        return json.loads(text,object_pairs_hook=strict_object,
                          parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite value')))
    if kind=='yaml':
        import yaml
        class Unique(yaml.SafeLoader):pass
        def mapping(loader,node):
            # Reject merge keys/duplicate keys instead of silently resolving precedence.
            return strict_object([(loader.construct_object(k,deep=True),loader.construct_object(v,deep=True)) for k,v in node.value])
        Unique.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)
        return yaml.load(text,Loader=Unique)
    raise ValueError('unsupported config format')


def pointer(obj,value):
    if not isinstance(value,str) or not value.startswith('/'):raise ValueError('explicit pointer required')
    for key in value.split('/')[1:]:
        key=key.replace('~1','/').replace('~0','~')
        if not isinstance(obj,dict):raise ValueError('pointer must traverse mappings')
        obj=obj[key]
    return obj


def capture(adapter):
    """Selectors refer only to adapter.config_files(); config contents never emitted.

The selected values are declarations, not verified effective runtime settings.
Unknown or malformed selectors retain null, with categorical errors only.
"""
    from engine.adapter import safe_config
    config=safe_config(adapter.config)
    selectors=adapter.config.get('runtime_identity_sources',{})
    if not isinstance(selectors,dict):selectors={}
    files={};records=[]
    for p in adapter.config_files():
        p=Path(p).absolute()
        if str(p) in files:raise ValueError('duplicate runtime config file')
        try:
            before=p.stat()
            if p.is_symlink() or before.st_size>8*1024*1024:raise ValueError('unsafe config file')
            data=p.read_bytes();after=p.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('config changed during read')
            files[str(p)]=data
            records.append(dict(path=str(p),exists=True,sha256=digest(data)))
        except (OSError,ValueError):
            files[str(p)]=None;records.append(dict(path=str(p),exists=False))
    values={'model':None,'temperature':None};sources={};errors=[]
    for field in values:
        try:
            sel=selectors[field]
            if not isinstance(sel,dict) or set(sel)!={'path','format','pointer'}:raise ValueError('invalid selector')
            path=str(Path(sel['path']).expanduser().absolute())
            data=files[path]
            if data is None:raise ValueError('config unavailable')
            value=pointer(decode(data,sel['format']),sel['pointer'])
            valid=(isinstance(value,str) and bool(value.strip()) if field=='model' else
                   type(value) in (float,int) and math.isfinite(value))
            if not valid:raise ValueError('invalid field type')
            values[field]=value;sources[field]=dict(path=path,format=sel['format'],pointer=sel['pointer'],sha256=digest(data))
        except Exception:
            errors.append(field+'_unresolved')
    root=Path(__file__).resolve().parents[1]
    adapter_file=Path(inspect.getfile(type(adapter))).resolve()
    code_files=[root/'engine'/n for n in ('adapter.py','runner.py','runtime_identity.py','collectors.py',
                'observation.py','memory_capture.py','redact.py','workspace.py','timeanchor.py','paths.py')]
    code={p.name:digest(p.read_bytes()) for p in code_files}
    config['adapter_version']='sha256:'+digest(adapter_file.read_bytes())
    config['executor_version']='sha256:'+digest(json.dumps(code,sort_keys=True).encode())
    if adapter.name=='openclaw' and hasattr(adapter,'workspace'):
        scope=dict(kind='configured_files',root=str(adapter.workspace.resolve()),paths=['MEMORY.md','USER.md','memory/*.md'])
    elif adapter.name=='hermes' and hasattr(adapter,'memories'):
        scope=dict(kind='configured_files',root=str(adapter.memories.resolve()),paths=['MEMORY.md','USER.md'])
    else:
        scope=dict(kind='undeclared',complete=False)
        errors.append('observation_scope_unresolved')
    scope.update(complete=False,instance_independently_verified=False)
    config['observation_scope']=scope
    provenance=dict(format='okb-runtime-identity-v1-draft',sources=sources,errors=errors,
                    effective_settings_verified=False,code_sha256=code)
    return dict(name=adapter.name,agent_kind=adapter.agent_kind,**values,config=config,
                capabilities=adapter.capabilities().as_dict(),config_files=records,
                event_coverage=adapter.event_coverage(),runtime_identity=provenance)


def unchanged(adapter, original):
    current=capture(adapter)
    return all(current[k]==original[k] for k in ('model','temperature','config','config_files','runtime_identity'))
