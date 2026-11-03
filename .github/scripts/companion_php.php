<?php

// What this PHP loads for a companion extension, for companion_origin.py php --installed and
// php_ext_musl.sh (the companion-pins spec, the origin table's PHP row):
//
//   php [-d extension=<file>] companion_php.php <extension>
//
// Prints one JSON object and nothing else, never a verdict: this PHP's version, whether the
// extension is loaded and the version it reports, and on Linux the files of that name mapped
// into the process (/proc/self/maps), which is the loaded file itself. The extension's name is
// an argument, never part of the code. Runs on every PHP the Hyper* extensions support (8.3
// and later) with nothing but the core.

$name = $argv[1] ?? '';
if (preg_match('/\A[a-z][a-z0-9_]*\z/', $name) !== 1) {
    fwrite(STDERR, 'companion_php.php: ' . var_export($name, true) . " is not an extension name\n");
    exit(2);
}
$loaded = extension_loaded($name);
$mapped = [];
if (@is_readable('/proc/self/maps')) {
    foreach (file('/proc/self/maps', FILE_IGNORE_NEW_LINES) ?: [] as $line) {
        $fields = preg_split('/\s+/', trim($line), 6);
        $path = $fields[5] ?? '';
        if ($path !== '' && str_contains(basename($path), $name)) {
            $mapped[$path] = true;
        }
    }
}
echo json_encode([
    'php' => PHP_VERSION,
    'loaded' => $loaded,
    'version' => $loaded ? (string) (new ReflectionExtension($name))->getVersion() : '',
    'mapped' => array_keys($mapped),
], JSON_UNESCAPED_SLASHES), "\n";
