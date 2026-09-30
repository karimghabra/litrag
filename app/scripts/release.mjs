// A release archive: the unpacked app from electron-builder beside the install scripts.
//   npm run release -- win      → release/litrag-<version>-win-x64.zip
//   npm run release -- linux    → release/litrag-<version>-linux-x64.tar.gz
// The platform defaults to this machine's. It builds first (`npm run dist:<platform>`: esbuild,
// then electron-builder's unpacked folder), which cross-builds Windows on Linux too, since
// electron-builder edits the exe's resources without wine; `--no-build` packs what release/
// already holds. The version is this package's. A person extracts the archive and runs
// install.cmd or install.sh (README, "Install"); the scripts expect app/ beside them.
//
// The zip is written here, with Node's own zlib, rather than by a zip tool, so the same
// command makes the same archive on Windows and on Linux. The tar.gz is GNU tar's, on Linux.
import { spawnSync } from 'node:child_process';
import { closeSync, cpSync, openSync, readFileSync, readdirSync, rmSync, statSync, mkdirSync, writeFileSync, writeSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import { crc32, deflateRawSync } from 'node:zlib';

const appDir = resolve(import.meta.dirname, '..');
const installDir = resolve(appDir, '..', 'install');
const outDir = join(appDir, 'release');
const args = process.argv.slice(2);
const platform = args.find((a) => !a.startsWith('-')) ?? { win32: 'win', linux: 'linux' }[process.platform];
if (platform !== 'win' && platform !== 'linux') {
  console.error(`release: a platform, win or linux (there is no ${process.platform} build yet)`);
  process.exit(2);
}
const { version } = JSON.parse(readFileSync(join(appDir, 'package.json'), 'utf8'));

if (!args.includes('--no-build')) {
  const r = spawnSync('npm', ['run', `dist:${platform}`], { cwd: appDir, stdio: 'inherit', shell: process.platform === 'win32' });
  if (r.status !== 0) process.exit(r.status ?? 1);
}

const unpacked = join(outDir, `${platform}-unpacked`);
const exe = join(unpacked, platform === 'win' ? 'litrag.exe' : 'litrag');
if (!exists(exe)) fail(`${relative(appDir, exe)} is missing: run npm run dist:${platform} first`);
if (!exists(join(unpacked, 'resources', 'parser', 'uv.lock'))) fail('resources/parser/uv.lock is missing from the build');

const name = `litrag-${version}-${platform}-x64`;
const stage = join(outDir, 'stage', name);
rmSync(join(outDir, 'stage'), { recursive: true, force: true });
mkdirSync(stage, { recursive: true });
cpSync(unpacked, join(stage, 'app'), { recursive: true, verbatimSymlinks: true });

// The scripts, with the line endings their shells want whatever the checkout gave them.
// Windows PowerShell 5.1 reads a script with no byte-order mark as the ANSI code page, so the
// .ps1 files must be ASCII: an em dash in a comment would reach it as three other characters.
const scripts = platform === 'win' ? ['install.cmd', 'install.ps1', 'uninstall.cmd', 'uninstall.ps1'] : ['install.sh', 'uninstall.sh'];
for (const s of scripts) {
  const text = readFileSync(join(installDir, s), 'utf8').replace(/\r\n/g, '\n');
  const bad = [...text].findIndex((c) => c.charCodeAt(0) > 0x7e || (c.charCodeAt(0) < 0x20 && c !== '\n' && c !== '\t'));
  if (s.endsWith('.ps1') || s.endsWith('.cmd')) {
    if (bad >= 0) fail(`install/${s} has a non-ASCII character at offset ${bad}; Windows PowerShell would misread it`);
    writeFileSync(join(stage, s), text.replace(/\n/g, '\r\n'));
  } else {
    writeFileSync(join(stage, s), text, { mode: 0o755 });
  }
}

const archive = join(outDir, platform === 'win' ? `${name}.zip` : `${name}.tar.gz`);
rmSync(archive, { force: true });
if (platform === 'win') {
  // flat: Windows' "Extract All" already makes a folder named after the zip
  writeZip(archive, stage);
} else {
  // one folder at the top, as a tarball should have
  const r = spawnSync('tar', ['-czf', archive, '--owner=0', '--group=0', '--numeric-owner', '-C', join(outDir, 'stage'), name], { stdio: 'inherit' });
  if (r.status !== 0) fail('tar failed');
}
rmSync(join(outDir, 'stage'), { recursive: true, force: true });
console.log(`${relative(process.cwd(), archive)}  ${(statSync(archive).size / 2 ** 20).toFixed(1)} MB`);

function exists(p) {
  try {
    statSync(p);
    return true;
  } catch {
    return false;
  }
}

function fail(why) {
  console.error(`release: ${why}`);
  process.exit(1);
}

/** Every file under `dir`, relative to it, with forward slashes. */
function walk(dir, base = dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    return e.isDirectory() ? walk(p, base) : [relative(base, p).split(sep).join('/')];
  });
}

/** A plain zip (deflate, no zip64: an Electron app is far under 4 GB and 65,535 entries) of the files under `dir`. */
function writeZip(file, dir) {
  const fd = openSync(file, 'w');
  const central = [];
  let offset = 0;
  const put = (buf) => {
    writeSync(fd, buf);
    offset += buf.length;
  };
  for (const path of walk(dir)) {
    const full = join(dir, path);
    const data = readFileSync(full);
    const packed = deflateRawSync(data, { level: 9 });
    const [method, body] = packed.length < data.length ? [8, packed] : [0, data];
    const nameBuf = Buffer.from(path, 'utf8');
    const utf8 = /[^\x20-\x7e]/.test(path) ? 0x0800 : 0;
    const m = statSync(full).mtime;
    const time = (m.getHours() << 11) | (m.getMinutes() << 5) | (m.getSeconds() >> 1);
    const date = ((Math.max(m.getFullYear(), 1980) - 1980) << 9) | ((m.getMonth() + 1) << 5) | m.getDate();
    const crc = crc32(data);
    if (offset > 0xffffffff || data.length > 0xffffffff) fail('the zip would need zip64');
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4); // version needed: 2.0 (deflate)
    local.writeUInt16LE(utf8, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt16LE(time, 10);
    local.writeUInt16LE(date, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    local.writeUInt16LE(0, 28);
    const at = offset;
    put(local);
    put(nameBuf);
    put(body);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4); // made by: MS-DOS, 2.0
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(utf8, 8);
    entry.writeUInt16LE(method, 10);
    entry.writeUInt16LE(time, 12);
    entry.writeUInt16LE(date, 14);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(body.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(nameBuf.length, 28);
    entry.writeUInt32LE(at, 42);
    central.push(entry, nameBuf);
  }
  const start = offset;
  if (central.length / 2 > 0xffff || start > 0xffffffff) fail('the zip would need zip64');
  for (const b of central) put(b);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(central.length / 2, 8);
  end.writeUInt16LE(central.length / 2, 10);
  end.writeUInt32LE(offset - start, 12);
  end.writeUInt32LE(start, 16);
  put(end);
  closeSync(fd);
}
