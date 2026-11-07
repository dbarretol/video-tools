# vtools

One command for every tool of [video-tools](../../README.md). It installs
[videothumb](../videothumb/README.md) and [videosemanticreducer](../videosemanticreducer/README.md)
together and passes each command to the tool that runs it:

| Command | Runs |
|---|---|
| `vtools sheet`, `frames`, `pack`, `gui` | `videothumb` |
| `vtools archive`, `fingerprint`, `compare`, `verify`, `status` | `videosemanticreducer` |
| `vtools config thumb ...`, `vtools config vsr ...` | `videothumb config ...`, `videosemanticreducer config ...` |
| `vtools PATH...` | the default task of videothumb (`sheet` unless the config file says otherwise) |

```bash
vtools movie.mp4                  # contact sheet
vtools pack ./videos -w 2         # sheet and frames of each video in one ZIP
vtools archive ./videos -o D:/Archive
vtools status ./videos -o D:/Archive --full
vtools config vsr set num_snapshots 100
```

Every option is the one of the tool that runs the command; `vtools COMMAND --help` lists them.
The tools keep their own commands too, with short aliases: `vthumb` for `videothumb` and `vsr` for
`videosemanticreducer`.
