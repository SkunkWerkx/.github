<?php

/**
 * The PHP extension leg's load check, run with the built extension named on the command line:
 *
 *   php -d extension=<built library> php_ext_load_check.php <crate> <package_version>
 *
 * A Zend extension that compiled can still be refused at load: PHP matches a module's build ID,
 * `API<number>,<NTS|ZTS>[,<compiler>]`, exactly, and a mismatch is a startup warning ("Module
 * compiled with module API=...") after which PHP runs on without the module. So this fails
 * unless the module loaded and reports <package_version>, the bare X.Y.Z of the crate it was
 * built from, and it prints the build ID the bytes are pinned to, which the zip records only
 * in its name.
 */

declare(strict_types=1);

[, $crate, $want] = $argv + [null, '', ''];
if ($crate === '' || $want === '') {
    fwrite(STDERR, "usage: php -d extension=<library> php_ext_load_check.php <crate> <package_version>\n");
    exit(2);
}

ob_start();
phpinfo(INFO_GENERAL);
$info = (string) ob_get_clean();
preg_match('/^PHP Extension Build => (\S+)\r?$/m', $info, $build);
preg_match('/^PHP Extension => (\d+)\r?$/m', $info, $api);
$line = sprintf('PHP %s, build ID %s, module API %s', PHP_VERSION, $build[1] ?? '(none)', $api[1] ?? '(none)');
echo $line, PHP_EOL;

if (!extension_loaded($crate)) {
    fwrite(STDERR, "::error::the {$crate} extension did not load into {$line}\n");
    exit(1);
}
$got = phpversion($crate);
if ($got !== $want) {
    fwrite(STDERR, sprintf("::error::phpversion('%s') is %s, not %s\n", $crate, var_export($got, true), $want));
    exit(1);
}
echo "{$crate} {$got} loaded", PHP_EOL;

$summary = getenv('GITHUB_STEP_SUMMARY');
if (is_string($summary) && $summary !== '') {
    file_put_contents($summary, "`{$crate}` {$got} — {$line}\n\n", FILE_APPEND);
}
