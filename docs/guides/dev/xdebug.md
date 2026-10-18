# Debug PHP with Xdebug

The bundled PHP image has Xdebug enabled in `debug` mode and uses `client_host=xdebug://gateway`. The usual debugger port is 9003. Profiling is not enabled by default.

## Connect your IDE

Configure your IDE to listen for Xdebug and map `/var/www/html/w` to your MediaWiki checkout. Xdebug connects from the PHP container to the IDE; the IDE must be reachable at its configured host and port.

`xdebug://gateway` resolves the container's current gateway on Linux inside Xdebug itself. There is no host IP to calculate or save, and network recreation does not leave a stale address. Docker Desktop, rootless Docker or a remote VM may need another reachable address. If your runtime provides `host.docker.internal`, for example:

```sh
mw dev env set MEDIAWIKI_XDEBUG_CONFIG 'client_host=host.docker.internal client_port=9003'
mw dev mediawiki create --force-recreate
```

Use an address appropriate to your setup. A public HTTP tunnel does not carry Xdebug's separate TCP connection; a remote environment needs suitable private connectivity to your IDE.

## Start a debug session

Use your IDE's browser integration to set a debugging cookie, or add `?XDEBUG_SESSION=1` to a wiki request. Debug mode enables support; a trigger starts the session. See [Xdebug's step-debugging documentation](https://xdebug.org/docs/step_debug).

For a CLI request, run from your MediaWiki checkout:

```sh
mw dev mediawiki exec -- XDEBUG_SESSION=1 php maintenance/run.php Version
```

For an IDE that needs an explicit server mapping, add `PHP_IDE_CONFIG='serverName=YOUR_SERVER'` before `php` in that command.

## Performance and profiling

Debug mode has runtime overhead even without a debugging trigger. An untriggered
request does not connect to an IDE, but PHP still pays the debugger's execution
cost. In the bundled image, enabling debug mode also disables OPcache's optimiser,
even without an active debugging session. For performance measurements or workloads where debugging is unnecessary,
use `MEDIAWIKI_XDEBUG_MODE=off` and recreate the PHP services as shown below.

Profiling is separate from step debugging. The default mode is only `debug`, so
neither ordinary requests nor a profiling trigger creates `cachegrind.out` files.
Do not enable `profile` as a general debugging prerequisite. A triggered debug
request with no reachable IDE still completes, but can log a connection failure
and wait up to Xdebug's connection timeout (normally 200 ms).

For a one-off CLI command, override the mode without recreating containers:

```sh
mw dev mediawiki exec -- XDEBUG_MODE=off composer phpunit tests/phpunit/unit/includes/Xml/XmlTest.php
mw dev mediawiki exec -- XDEBUG_MODE=debug XDEBUG_SESSION=1 php maintenance/run.php Version
```

These overrides affect only that command. Web requests keep the PHP service's
configured mode until you recreate it.

## Inspect or disable Xdebug

```sh
mw dev mediawiki exec -- php --ri xdebug
mw dev env set MEDIAWIKI_XDEBUG_MODE off
mw dev mediawiki create --force-recreate
```

Restore step debugging with:

```sh
mw dev env set MEDIAWIKI_XDEBUG_MODE debug
mw dev mediawiki create --force-recreate
```

These environment changes also apply to the jobrunner. If it is running, recreate it with `mw dev mediawiki jobrunner create --force-recreate`.

`MEDIAWIKI_XDEBUG_MODE` and `MEDIAWIKI_XDEBUG_CONFIG` are the CLI settings; Compose passes them to PHP as `XDEBUG_MODE` and `XDEBUG_CONFIG`. Inspect the effective modes and host settings with `php --ri xdebug`. To enable profiling as well, select `debug,profile` and configure an output location and triggers as described in [Xdebug's profiling guide](https://xdebug.org/docs/profiler).
