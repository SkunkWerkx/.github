<?php

/**
 * The asset names PIE itself would accept for the PHP running this script, from the phar's own
 * code (php_ext_package.py's check 3).
 *
 *     php pie_asset_names.php <pie.phar> <version> <extension>
 *
 * Prints one name per line: PrePackagedBinaryAssetName::packageNames() on Unix, or on Windows
 * WindowsExtensionAssetName's zip names and then the DLL names PIE looks for inside the zip.
 * The package is a stub of type php-ext for <extension> (the consumer's crate) at <version>;
 * the names come from the extension name and the version, never the package name. It is built
 * through PIE's own Package::fromComposerCompletePackage(), and the platform
 * comes from TargetPlatform::fromPhpBinaryPath() for this PHP, as `pie install` builds both.
 *
 * These classes are @internal to PIE. One that is gone, or a method that moved, exits 2 with
 * "pie_asset_names: PIE internals changed: <class>", so a PIE upgrade never passes by default.
 */

declare(strict_types=1);

use Composer\Package\CompletePackage;
use Composer\Semver\VersionParser;
use Php\Pie\DependencyResolver\Package;
use Php\Pie\Platform\OperatingSystem;
use Php\Pie\Platform\PrePackagedBinaryAssetName;
use Php\Pie\Platform\TargetPhp\PhpBinaryPath;
use Php\Pie\Platform\TargetPlatform;
use Php\Pie\Platform\WindowsExtensionAssetName;

$fail = static function (string $message, int $status): never {
    fwrite(STDERR, 'pie_asset_names: ' . $message . PHP_EOL);
    exit($status);
};

if ($argc !== 4) {
    $fail('usage: php pie_asset_names.php <pie.phar> <version> <extension>', 1);
}
[, $phar, $version, $extension] = $argv;
if (! preg_match('/^[a-z][a-z0-9_]*$/', $extension)) {
    $fail('the extension is a lowercase PHP extension name, not ' . var_export($extension, true), 1);
}
$phar = realpath($phar);
if ($phar === false) {
    $fail('no PIE phar at ' . $argv[1], 1);
}

// As PIE's own bin/pie does for a phar build: its dependencies' deprecations are not ours.
error_reporting(error_reporting() & ~E_DEPRECATED);
$autoload = 'phar://' . $phar . '/vendor/autoload.php';
if (! is_file($autoload)) {
    $fail('PIE internals changed: vendor/autoload.php', 2);
}
require $autoload;

foreach (
    [
        CompletePackage::class,
        VersionParser::class,
        Package::class,
        OperatingSystem::class,
        PrePackagedBinaryAssetName::class,
        PhpBinaryPath::class,
        TargetPlatform::class,
        WindowsExtensionAssetName::class,
    ] as $class
) {
    if (! class_exists($class) && ! enum_exists($class)) {
        $fail('PIE internals changed: ' . $class, 2);
    }
}

try {
    $normalized = (new VersionParser())->normalize($version);
    $composerPackage = new CompletePackage('skunkwerkx/' . $extension, $normalized, $version);
    $composerPackage->setType('php-ext');
    $composerPackage->setPhpExt([
        'extension-name' => $extension,
        'support-zts' => false,
        'support-nts' => true,
        'download-url-method' => ['pre-packaged-binary'],
        'os-families' => ['linux', 'darwin', 'windows'],
    ]);
    $package = Package::fromComposerCompletePackage($composerPackage);

    $targetPlatform = TargetPlatform::fromPhpBinaryPath(PhpBinaryPath::fromCurrentProcess(), 1, null);

    $names = $targetPlatform->operatingSystem === OperatingSystem::Windows
        ? [...WindowsExtensionAssetName::zipNames($targetPlatform, $package),
            ...WindowsExtensionAssetName::dllNames($targetPlatform, $package)]
        : PrePackagedBinaryAssetName::packageNames($targetPlatform, $package);
} catch (Error $error) {
    // A method or signature that moved: the same break as a missing class, never a pass.
    $fail('PIE internals changed: ' . $error::class . ': ' . $error->getMessage(), 2);
}

echo implode(PHP_EOL, $names), PHP_EOL;
