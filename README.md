# Meridies — intro experience

The WebGL intro, split out of the single-file artifact into deployable files.
No build step, no dependencies, no API keys.

```
meridies-intro/
├── index.html        5 KB   markup only
├── styles.css        8 KB
├── app.js           46 KB   WebGL stage, spatial audio, intro sequence
└── assets/
    └── logo.png    285 KB   was a base64 data URI inside app.js
```

Total 348 KB, down from one 439 KB file. The logo is now a real PNG, so the
browser caches it separately and the JS parses much faster.

## Run it locally

It must be served over HTTP. Opening `index.html` from the file system will
fail to load the logo in some browsers.

```bash
cd meridies-intro
python3 -m http.server 8080
# then open http://localhost:8080
```

## Deploy

**Vercel**
```bash
npm i -g vercel
cd meridies-intro
vercel --prod        # framework: Other. Nothing to build.
```

**Netlify** — drag the `meridies-intro` folder onto app.netlify.com/drop.

**GitHub Pages** — commit the folder, enable Pages, point it at that folder.

## Before you ship

- [ ] Open it with headphones and confirm the three memories are audibly in
      different directions. This is the core product claim.
- [ ] Test "Enter without sound" — judges may not have headphones.
- [ ] Test on laptop speakers. Does it still make sense?
- [ ] Test with a screen reader. This is a product for blind people.
- [ ] Check "Replay intro" works after the sequence ends.
- [ ] Try it on the venue wifi. 348 KB should be fine, but confirm.

## Editing

| What | Where |
|---|---|
| The three memories (text, position, audio) | `MEMS` at the top of `app.js` |
| Real recordings instead of silence | set `audio` on a `MEMS` entry to a data URI, or change it to a file path and put the file in `assets/` |
| The four feature names | the `.feat` blocks in `index.html` |
| Colours and type | `:root` in `styles.css` |

`MEMS` entries carry `x, y, z` in metres relative to the listener, so moving a
memory moves where you hear it from.

## Note on the audio

The page creates its `AudioContext` on the first click, which is why the gate
screen exists — browsers block audio until a user gesture. "Enter without
sound" skips straight past it.
