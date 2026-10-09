"""One compatibility policy for listing, reusing and starting terrain artifacts."""
def outdated_reasons(manifest, schema, engine, source_config):
    reasons = []
    if manifest.get('schema') != schema:
        reasons.append('terrain_format')
    if manifest.get('posim_commit') != engine:
        reasons.append('posim_engine')
    if manifest.get('source_config') != source_config:
        reasons.append('terrain_sources')
    return reasons
