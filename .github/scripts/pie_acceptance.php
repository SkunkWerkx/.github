<?php

// What hyper-pie-acceptance.yml asserts once `pie install` has run: run by the PHP PIE installed
// into, in a fresh process with no -d flags, so the extension is loaded only if PIE enabled it.
//
//     php pie_acceptance.php <extension> <package_version> [<entry_sha256>]
//
// <extension> is the extension's name (the consumer's crate), and <package_version> the bare
// X.Y.Z phpversion(<extension>) reports. <entry_sha256>, when given, is the sha256 of the one
// file in the Release's zip for this leg, which the workflow downloaded and verified: the file
// PIE installed into extension_dir must be those bytes (on Windows, where PIE renames the DLL,
// some DLL there must be).
//
// Loading, the version and the bytes hold for any extension. The API checks are HyperUuid's PHP
// surface, the only extension the Forge packs today: its v5 and SQL order vectors are
// corpus/v5.json's and corpus/sql_order.json's (the consumer's corpus, add-only), so a zip that
// loads but computes wrong fails here too. Another extension fails until it has checks of its own.

declare(strict_types=1);

use HyperUuid\HyperUuid;
use HyperUuid\Namespaces;
use HyperUuid\Uuid;
use HyperUuid\UuidLayout;
use HyperUuid\UuidVariant;

// corpus/v5.json: {"namespace": "dns", "name": "www.example.com", "expect": "2ed6657de927568b95e12665a8aea6a2"}
const V5_NAME = 'www.example.com';
const V5_EXPECT = '2ed6657d-e927-568b-95e1-2665a8aea6a2';
// corpus/sql_order.json: {"rfc": "017f22e279b070008000090807060504", "sql": "07060504090800808000017f22e279b0"}
const SQL_RFC = '017f22e2-79b0-7000-8000-090807060504';
const SQL_ORDER = '07060504-0908-0080-8000-017f22e279b0';

$failures = [];
$check = static function (string $what, bool $ok) use (&$failures): void {
    echo ($ok ? 'ok   ' : 'FAIL ') . $what . "\n";
    if (!$ok) {
        $failures[] = $what;
    }
};

if (
    ($argc !== 3 && $argc !== 4) || !preg_match('/^[a-z][a-z0-9_]*$/', $argv[1])
    || !preg_match('/^\d+\.\d+\.\d+$/', $argv[2]) || ($argc === 4 && !preg_match('/^[0-9a-f]{64}$/', $argv[3]))
) {
    fwrite(STDERR, "usage: php pie_acceptance.php <extension> <package_version (bare X.Y.Z)> [<entry_sha256>]\n");
    exit(2);
}
[, $extension, $version] = $argv;
$entrySha256 = $argv[3] ?? null;

echo 'PHP ' . PHP_VERSION . (PHP_ZTS ? ' ZTS' : ' NTS') . ' on ' . PHP_OS_FAMILY . ' ' . php_uname('m')
    . ', ini ' . (php_ini_loaded_file() ?: '(none)') . "\n";
if (!extension_loaded($extension)) {
    $check("extension_loaded('{$extension}'): PIE installed it but did not enable it", false);
    exit(1);
}
$check("extension_loaded('{$extension}')", true);
$check("phpversion('{$extension}') === " . var_export($version, true)
    . ' (got ' . var_export(phpversion($extension), true) . ')', phpversion($extension) === $version);

if ($entrySha256 !== null) {
    $dir = (string) ini_get('extension_dir');
    $candidates = PHP_OS_FAMILY === 'Windows'
        ? (glob($dir . DIRECTORY_SEPARATOR . '*.dll') ?: [])
        : [$dir . DIRECTORY_SEPARATOR . $extension . '.so'];
    $installed = array_values(array_filter(
        $candidates,
        static fn (string $file): bool => is_file($file) && hash_file('sha256', $file) === $entrySha256
    ));
    $check('the installed file is the Release zip\'s entry, sha256 ' . $entrySha256 . ' ('
        . ($installed ? $installed[0] : 'none of ' . implode(', ', $candidates) . ' in ' . $dir) . ')', $installed !== []);
}

if ($extension !== 'hyperuuid') {
    $check("API checks for '{$extension}': pie_acceptance.php has HyperUuid's only", false);
    fwrite(STDERR, count($failures) . " acceptance check(s) failed\n");
    exit(1);
}
$check('HyperUuid::nativeVersion() === the package version', HyperUuid::nativeVersion() === $version);

$v4 = HyperUuid::newV4();
$check('v4: version 4, RFC 9562 variant, parses back', $v4->version() === 4
    && $v4->variant() === UuidVariant::Rfc9562 && Uuid::parse((string) $v4)->equals($v4));

$v5 = HyperUuid::newV5(Namespaces::dns(), V5_NAME);
$check('v5: newV5(Namespaces::dns(), \'' . V5_NAME . '\') === ' . V5_EXPECT . ' (got ' . $v5 . ')',
    (string) $v5 === V5_EXPECT);

$v7 = HyperUuid::newV7(1700000000000);
$batch = array_map('strval', HyperUuid::newV7Batch(3));
$sorted = $batch;
sort($sorted, SORT_STRING);
$check('v7: version 7, unixMillis 1700000000000, a batch in strict order', $v7->version() === 7
    && $v7->unixMillis() === 1700000000000 && $batch === $sorted && count(array_unique($batch)) === 3);

$rfc = Uuid::parse(SQL_RFC);
$sql = $rfc->toSqlOrder();
$fresh = HyperUuid::newV7();
$check('SQL order: ' . SQL_RFC . ' -> ' . SQL_ORDER . ' (got ' . $sql . ') and back, and a fresh v7 round trip',
    (string) $sql === SQL_ORDER && $sql->fromSqlOrder()->equals($rfc)
    && $sql->version(UuidLayout::SqlServer) === 7
    && $fresh->toSqlOrder()->fromSqlOrder()->equals($fresh));

if ($failures) {
    fwrite(STDERR, count($failures) . " acceptance check(s) failed\n");
    exit(1);
}
echo "all acceptance checks passed\n";
