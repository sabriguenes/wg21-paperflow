"""Metadata extraction and YAML front matter formatting for WG21 papers.

Imports are deferred to avoid circular dependency with tomd.lib.shared.
Use direct imports from submodules:
  from tomd.lib.metadata_yaml.format import format_front_matter
  from tomd.lib.metadata_yaml.extract import extract_metadata
  from tomd.lib.metadata_yaml.strip import strip_metadata_headings
"""
