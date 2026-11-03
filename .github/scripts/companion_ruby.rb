# What this Ruby loads for a companion gem, for companion_origin.py ruby --installed and
# companion_container.sh's companion_ruby (the companion-pins spec, the origin table's Ruby row):
#
#   ruby companion_ruby.rb <gem>                 (under `bundle exec` where the leg uses Bundler)
#
# Requires the gem as a consumer does, then prints one key=value line per fact and nothing else,
# never a verdict: which file the gem's <gem>.rb came from, the version and backend it reports,
# the Magnus extension it loaded (its path and SHA-256, from $LOADED_FEATURES), and, on the
# Fiddle backend, the shared library it opened: the load is forced (native_version, the
# cheapest door), the path is the one the gem's own search answers (its Runtime.library_path,
# what the load itself calls, else Interop.library_path over the gem's native/), with its
# SHA-256, and `mapped` lists every copy of that library this process has mapped, read from
# /proc/self/maps on Linux and dyld's image list on macOS (`observed` is 1 where either was
# read). Also where RubyGems or Bundler activated the gem from. The gem's name is an argument,
# never part of the code. Runs on every Ruby the Hyper* gems support (3.3 and later) and needs
# nothing outside the standard library.
require "digest"

name = ARGV.fetch(0)
abort "companion_ruby.rb: #{name.inspect} is not a gem name" unless name.match?(/\A[a-z][a-z0-9_]*\z/)
require name

constant = Object.constants.find { |c| c.to_s.downcase == name }
abort "companion_ruby.rb: requiring #{name} defined no module named for it" unless constant
mod = Object.const_get(constant)
source = $LOADED_FEATURES.find { |f| File.basename(f) == "#{name}.rb" }
extension = $LOADED_FEATURES.find { |f| File.basename(f).match?(/\A#{name}_native\.(so|bundle|dll)\z/) }
backend = mod.const_defined?(:BACKEND) ? mod::BACKEND : ""
rid = mod.const_defined?(:NativePlatform) ? mod::NativePlatform.rid_and_library_name(library: name).first : nil

library = nil
mapped = []
observed = false
if backend == :fiddle && mod.respond_to?(:native_version)
  mod.native_version
  runtime = mod.const_defined?(:Runtime) ? mod::Runtime : nil
  library =
    if runtime&.respond_to?(:library_path, true)
      runtime.send(:library_path)
    elsif source && mod.const_defined?(:Interop)
      mod::Interop.library_path(name, File.join(File.dirname(source), name, "native"),
                                File.expand_path("../..", File.dirname(source)))
    end
  file = /\A(lib)?#{name}\.(so|dylib|dll)\z/
  if File.readable?("/proc/self/maps")
    observed = true
    mapped = File.foreach("/proc/self/maps").map { |line| line.split(" ", 6)[5].to_s.strip }
  elsif RUBY_PLATFORM.include?("darwin")
    require "fiddle"
    observed = true
    images = Fiddle::Handle::DEFAULT
    count = Fiddle::Function.new(images["_dyld_image_count"], [], Fiddle::TYPE_UINT32_T).call
    image = Fiddle::Function.new(images["_dyld_get_image_name"], [Fiddle::TYPE_UINT32_T], Fiddle::TYPE_VOIDP)
    mapped = (0...count).map { |i| image.call(i).to_s }
  end
  mapped = mapped.select { |path| File.basename(path).match?(file) && File.file?(path) }
                 .map { |path| File.realpath(path) }.uniq
end
sha = ->(path) { path && File.file?(path) ? Digest::SHA256.file(path).hexdigest : "" }

facts = {
  "name" => name,
  "version" => mod.const_defined?(:VERSION) ? mod::VERSION : "",
  "ruby" => RUBY_VERSION,
  "abi" => RUBY_VERSION[/\A\d+\.\d+/],
  "rid" => rid || "",
  "backend" => backend,
  "pure" => ENV.key?("#{name.upcase}_PURE") ? "1" : "",
  "source" => source ? File.realpath(source) : "",
  "extension" => extension ? File.realpath(extension) : "",
  "extension_sha256" => sha.call(extension),
  "library" => library && File.file?(library) ? File.realpath(library) : "",
  "library_sha256" => sha.call(library),
  "observed" => observed ? "1" : "",
  "mapped" => mapped.join(","),
  "gem" => (spec = Gem.loaded_specs[name]) ? File.realpath(spec.full_gem_path) : ""
}
facts.each { |key, value| puts "#{key}=#{value}" }
