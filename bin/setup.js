#!/usr/bin/env node
'use strict';

/**
 * One-time (per machine, per requirements change) setup for the npm install:
 * creates a private Python environment in ~/.termify/venv and installs the
 * audio stack there - yt-dlp, imageio-ffmpeg (bundled ffmpeg), numpy,
 * sounddevice and prompt_toolkit.
 *
 * Runs automatically as the package's `postinstall`, and again from
 * `termify.js` on first launch if the postinstall was skipped or failed.
 *
 * Flags:  --check   report status without installing anything
 *         --force   reinstall dependencies even if they look up to date
 */

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const c = require('./common');

const IS_POSTINSTALL = process.env.npm_lifecycle_event === 'postinstall';

function run(cmd, args) {
  return spawnSync(cmd, args, {
    stdio: 'inherit',
    cwd: c.PKG_ROOT,
    windowsHide: true,
  });
}

function status() {
  const py = c.venvPython();
  return [
    `package:      ${c.PKG_ROOT}`,
    `python env:   ${py || '(not created yet)'}`,
    `env location: ${c.VENV_DIR}`,
    `data folder:  ${c.DATA_DIR}`,
    `deps match:   ${c.depsMatch() ? 'yes' : 'no'}`,
    `ready:        ${c.isReady() ? 'yes' : 'no'}`,
  ].join('\n');
}

function main() {
  if (process.argv.includes('--check')) {
    console.log(status());
    return c.isReady() ? 0 : 2;
  }
  if (process.env.TERMIFY_SKIP_SETUP === '1') {
    c.log('TERMIFY_SKIP_SETUP=1 - skipping the Python environment setup.');
    return 0;
  }
  if (c.isReady() && !process.argv.includes('--force')) {
    c.log('Python environment is already set up.');
    return 0;
  }

  c.log(status());

  const py = c.findPython();
  if (!py) {
    console.error(`[termify] No Python 3.10+ found on this machine.\n${c.PYTHON_HINT}`);
    return IS_POSTINSTALL ? 0 : 1;
  }
  const pyLabel = [py.cmd, ...py.args].join(' ');

  if (!fs.existsSync(c.VENV_DIR)) {
    c.log(`Creating a private Python environment in ${c.VENV_DIR} (found ${pyLabel}) ...`);
    const created = run(py.cmd, [...py.args, '-m', 'venv', c.VENV_DIR]);
    if (created.status !== 0) {
      console.error(
        [
          '[termify] Could not create the Python environment.',
          '  Debian/Ubuntu: sudo apt install python3-venv',
          '  Then run `termify` again.',
          '',
        ].join('\n')
      );
      return IS_POSTINSTALL ? 0 : 1;
    }
  }

  const vpy = c.venvPython();
  if (!vpy) {
    console.error('[termify] The environment was created but is missing a Python executable.');
    return IS_POSTINSTALL ? 0 : 1;
  }

  const installArgs = [
    '-m',
    'pip',
    'install',
    '-r',
    c.REQUIREMENTS,
    '--disable-pip-version-check',
    '--upgrade-strategy',
    'only-if-needed',
  ];
  c.log('Installing the audio stack in the background environment (this can take a minute) ...');
  let result = run(vpy, installArgs);
  if (result.status !== 0) {
    c.log('pip hit a problem - upgrading pip and retrying once ...');
    run(vpy, ['-m', 'pip', 'install', '-U', 'pip', '--disable-pip-version-check']);
    result = run(vpy, installArgs);
  }
  if (result.status !== 0) {
    console.error(
      [
        '[termify] Installing the Python dependencies failed.',
        '  Check your network / proxy and run `termify` again to retry.',
        '',
      ].join('\n')
    );
    return IS_POSTINSTALL ? 0 : 1;
  }

  const check = spawnSync(vpy, ['-c', 'import numpy, sounddevice, prompt_toolkit, yt_dlp, imageio_ffmpeg'], {
    stdio: 'inherit',
    windowsHide: true,
  });
  if (check.status !== 0) {
    console.error('[termify] Dependencies installed but the import check failed - run `termify` again to retry.');
    return IS_POSTINSTALL ? 0 : 1;
  }

  fs.mkdirSync(c.VENV_DIR, { recursive: true });
  fs.writeFileSync(c.markerPath(), c.requirementsHash());
  c.log('Setup complete. Just run: termify');
  return 0;
}

process.exit(main());
