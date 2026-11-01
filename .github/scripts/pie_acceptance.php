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
// Loading, the version and the bytes hold for any extension. The API checks are each consumer's
// own, keyed by the extension, and their vectors are lines of that consumer's corpus (add-only),
// so a zip that loads but computes wrong fails here too: HyperUuid's v5 and SQL order vectors
// (corpus/v5.json, corpus/sql_order.json), HyperCast's i32 verdicts (corpus/integer.json) and
// HyperTabular's faulted-cell case (corpus/delimited.json, "a cell that does not cast says
// where"), which also needs the companion ext-hypercast that pie_acceptance.sh installed first.
// An extension with no checks here fails rather than passing on checks that aren't its own.

declare(strict_types=1);

use HyperCast\Cast;
use HyperCast\Fault;
use HyperCast\NumFormat;
use HyperCast\Success;
use HyperTabular\Column;
use HyperTabular\DelimitedReader;
use HyperTabular\Dialect;
use HyperTabular\Tabular;
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
// corpus/delimited.json, "a cell that does not cast says where": its input, and each row's
// verdicts for the plan [i32(0), u8(1), bool(2), u8(0)] as verdict() renders them.
const TABULAR_INPUT = "a,b,c\n12x4,256,x\n -1,7,no\n";
const TABULAR_ROWS = [
    ['Malformed 2+1', 'OutOfRange 0+3', 'Malformed 0+1', 'Malformed 2+1'],
    ['ok -1', 'ok 7', 'ok false', 'OutOfRange 1+2'],
];

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

// A verdict as one comparable string: `ok <value>` or `<reason> <offset>+<length>`.
$verdict = static fn (Success|Fault $v): string => $v instanceof Success
    ? 'ok ' . var_export($v->value, true)
    : $v->reason->name . ' ' . $v->offset . '+' . $v->length;

$checks = [];
$checks['hyperuuid'] = static function () use ($check, $version): void {
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
};

$checks['hypercast'] = static function () use ($check, $version, $verdict): void {
    $check('Cast::nativeVersion() === the package version', Cast::nativeVersion() === $version);
    // corpus/integer.json: {"type": "i32", "input": "(1,234)", "expect": "ok", "value": -1234}
    $ok = $verdict(Cast::i32('(1,234)', NumFormat::invariant()));
    $check("i32 '(1,234)': ok -1234 (got {$ok})", $ok === 'ok -1234');
    // corpus/integer.json: {"type": "i32", "input": "12.5", "expect": "malformed", "fault": [2, 1]}
    $fault = $verdict(Cast::i32('12.5', NumFormat::invariant()));
    $check("i32 '12.5': Malformed 2+1, a Fault and not an exception (got {$fault})", $fault === 'Malformed 2+1');
};

$checks['hypertabular'] = static function () use ($check, $version, $verdict): void {
    // The companion: every cell is judged by ext-hypercast's classes.
    $check("extension_loaded('hypercast'), the companion", extension_loaded('hypercast'));
    if (!extension_loaded('hypercast')) {
        return;
    }
    $check('Tabular::nativeVersion() === the package version', Tabular::nativeVersion() === $version);
    $plan = [Column::i32(0), Column::u8(1), Column::bool(2), Column::u8(0)];
    $reader = DelimitedReader::fromString(TABULAR_INPUT, Dialect::csv(), $plan);
    $rows = [];
    foreach ($reader->rows() as $row) {
        $rows[] = array_map(static fn (int $c): string => $verdict($row->get($c)), [0, 1, 2, 3]);
    }
    $reader->close();
    $check('delimited "a cell that does not cast says where": ' . json_encode($rows), $rows === TABULAR_ROWS);
};

if (!isset($checks[$extension])) {
    $check("API checks for '{$extension}': pie_acceptance.php has none", false);
    fwrite(STDERR, count($failures) . " acceptance check(s) failed\n");
    exit(1);
}
$checks[$extension]();

if ($failures) {
    fwrite(STDERR, count($failures) . " acceptance check(s) failed\n");
    exit(1);
}
echo "all acceptance checks passed\n";
