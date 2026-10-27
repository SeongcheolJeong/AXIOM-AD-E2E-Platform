#!/usr/bin/env python3
"""Preview/apply an explicit sealed-result selection without restarting services."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))

from ade2e.retained_experiment_import import (RetainedExecutionSelection, RetainedImportError,
                                             retain_experiment_executions)


def selection(value):
    try:
        identity, checksum = value.split(':', 1)
        return RetainedExecutionSelection(identity, checksum)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use exact job_ID:original_receipt_sha256') from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-data-dir', type=Path, required=True)
    parser.add_argument('--target-data-dir', type=Path, required=True)
    parser.add_argument('--project', required=True, help='Existing project ID; no remapping or role changes')
    parser.add_argument('--source-label', required=True, help='Public-safe retained source description')
    parser.add_argument('--execution', type=selection, action='append', required=True,
                        help='Repeat exact job_ID:original_receipt_sha256 for each explicitly selected result')
    parser.add_argument('--receipt', type=Path, help='New private operator receipt, required with --apply')
    parser.add_argument('--apply', action='store_true', help='Publish the verified selection; default only previews')
    args = parser.parse_args()
    try:
        report = retain_experiment_executions(source_data_dir=args.source_data_dir, target_data_dir=args.target_data_dir,
            project_id=args.project, selections=args.execution, source_label=args.source_label,
            receipt_path=args.receipt, apply=args.apply)
    except RetainedImportError as exc:
        parser.exit(1, f'Retained import refused: {exc}\n')
    # Private root paths stay in the 0600 receipt, not terminal output.
    print(json.dumps({'operation': report['operation'], 'selection_sha256': report['selection_sha256'],
                      'public': report['public']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
