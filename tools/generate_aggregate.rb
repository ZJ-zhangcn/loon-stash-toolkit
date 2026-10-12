require "json"
require "yaml"

ROOT = File.expand_path("..", __dir__)
mappings = JSON.parse(File.read(File.join(ROOT, "sources/loon-plugins.json")))
paths = mappings.map { |item| File.join(ROOT, "stash", item.fetch("output")) }
paths << File.join(ROOT, "stash/Bilibili_remove_ads.stoverride")
metadata_keys = %w[name desc author homepage icon category date version]


def merge_values(target, source, path = "")
  source.each do |key, value|
    next_path = path.empty? ? key : "#{path}.#{key}"
    if value.is_a?(Hash)
      target[key] ||= {}
      raise "type conflict at #{next_path}" unless target[key].is_a?(Hash)
      merge_values(target[key], value, next_path)
    elsif value.is_a?(Array)
      target[key] ||= []
      raise "type conflict at #{next_path}" unless target[key].is_a?(Array)
      target[key].concat(value)
    elsif !target.key?(key)
      target[key] = value
    elsif target[key] != value
      raise "scalar conflict at #{next_path}: #{target[key].inspect} vs #{value.inspect}"
    end
  end
end

result = {
  "name" => "Loon 覆写合集",
  "desc" => "聚合本仓库 #{mappings.length} 个 Loon 来源覆写及 1 个 Bilibili 衍生覆写。单项文件仍保留在仓库中。",
  "author" => "各来源插件作者；合集整理：ZJ-zhangcn",
  "homepage" => "https://github.com/ZJ-zhangcn/loon-stash-toolkit",
  "icon" => "https://github.com/luestr.png",
  "category" => "插件合集"
}

paths.each do |path|
  document = YAML.safe_load(File.read(path))
  raise "invalid override mapping: #{path}" unless document.is_a?(Hash)
  merge_values(result, document.reject { |key, _| metadata_keys.include?(key) })
end

priority_paths = paths.first(2)
priority_rules = priority_paths.flat_map { |path| YAML.safe_load(File.read(path)).fetch("rules", []) }
raise "priority rules are not first" unless result.fetch("rules", []).first(priority_rules.length) == priority_rules

output_path = File.join(ROOT, "stash/Kelee_All.stoverride")
existing = YAML.safe_load(File.read(output_path)) if File.file?(output_path)
result["date"] = if existing.is_a?(Hash) && existing.reject { |key, _| key == "date" } == result
                   existing["date"]
                 else
                   Time.now.strftime("%Y-%m-%d %H:%M:%S")
                 end
output = YAML.dump(result, line_width: -1).sub(/\A---\s*\n/, "")
lines = output.lines
index = 0
while index < lines.length
  match = /\A( *)([^-\s].*?):\s*\r?\n?\z/.match(lines[index])
  if match
    key_indent = match[1].length
    sequence_indent = " " * key_indent
    next_index = index + 1
    if next_index < lines.length && lines[next_index].start_with?(sequence_indent + "-")
      end_index = next_index
      while end_index < lines.length
        line = lines[end_index]
        break if !line.strip.empty? && line[/\A */].length <= key_indent && !line.start_with?(sequence_indent + "-")
        end_index += 1
      end
      (next_index...end_index).each { |line_index| lines[line_index] = "  " + lines[line_index] }
      index = end_index
      next
    end
  end
  index += 1
end
output = lines.join.gsub(/^[ \t]+$/, "")
raise "YAML round-trip changed aggregate" unless YAML.safe_load(output) == result

unless File.file?(output_path) && File.read(output_path) == output
  File.write(output_path, output)
  puts "wrote #{output_path}"
else
  puts "aggregate unchanged"
end
puts "rules=#{result.dig('rules')&.length || 0} mitm=#{result.dig('http', 'mitm')&.length || 0} url=#{result.dig('http', 'url-rewrite')&.length || 0} body=#{result.dig('http', 'body-rewrite')&.length || 0} mocks=#{result.dig('http', 'mock')&.length || 0} scripts=#{result.dig('http', 'script')&.length || 0} providers=#{result.dig('script-providers')&.length || 0}"
