/**
 * The collect window: one Electron BrowserWindow that walks the wanted list.
 *
 * The CLI hands it a job file (papers, links, inbox). The user signs in and
 * clicks the PDF; the moment a download STARTS it is routed into the inbox
 * under the paper's key and the window moves to the next paper while the
 * download streams. The session partition persists, so an institutional
 * sign-in survives across papers and across runs. Popups are folded back
 * into the one window. Progress is JSON lines on stdout, for the CLI.
 *
 * Plain CommonJS: Electron's main process runs it without the TypeScript
 * transform the CLI uses.
 */

const { app, BrowserWindow, Menu, session } = require('electron');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const job = JSON.parse(readFileSync(process.argv[process.argv.length - 1], 'utf8'));
const say = (event) => process.stdout.write(`${JSON.stringify(event)}\n`);

let index = 0;
let caught = 0;
let finished = false;
let win;

function show() {
  const p = job.papers[index];
  win.setTitle(`${index + 1}/${job.papers.length} — ${p.title}`);
  say({ event: 'open', n: index + 1, of: job.papers.length, key: p.key, title: p.title });
  // Publisher redirect chains abort loads mid-flight; the window keeps what landed.
  win.loadURL(p.link).catch(() => {});
}

function advance() {
  index += 1;
  if (index >= job.papers.length) return finish();
  show();
}

function finish() {
  if (finished) return;
  finished = true;
  say({ event: 'done', caught });
  app.quit();
}

app.whenReady().then(() => {
  const ses = session.fromPartition('persist:litrag-collect');
  ses.on('will-download', (_event, item) => {
    const p = job.papers[Math.min(index, job.papers.length - 1)];
    item.setSavePath(join(job.inboxDir, p.file));
    caught += 1;
    say({ event: 'download', key: p.key, file: p.file });
    item.once('done', (_e, state) => say({ event: state === 'completed' ? 'saved' : 'failed', key: p.key, file: p.file }));
    advance();
  });

  win = new BrowserWindow({
    width: 1200,
    height: 900,
    webPreferences: { partition: 'persist:litrag-collect', sandbox: true },
    // No `plugins: true`: without the PDF viewer, an inline PDF becomes a download.
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    win.loadURL(url).catch(() => {});
    return { action: 'deny' };
  });
  win.on('closed', finish);

  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: 'Collect',
        submenu: [
          { label: 'Skip paper', accelerator: 'CmdOrCtrl+Right', click: () => { say({ event: 'skip', key: job.papers[index] && job.papers[index].key }); advance(); } },
          { label: 'Back', accelerator: 'Alt+Left', click: () => win.webContents.navigationHistory.goBack() },
          { label: 'Reopen paper page', accelerator: 'CmdOrCtrl+Home', click: () => show() },
          { type: 'separator' },
          { role: 'quit' },
        ],
      },
      { role: 'editMenu' },
      { role: 'viewMenu' },
    ]),
  );
  show();
});

app.on('window-all-closed', finish);
