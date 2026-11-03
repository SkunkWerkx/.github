# companion_origin.py r's probe: the copy of a package this R loads, and the DESCRIPTION fields
# of that copy the proof reads. Run as `Rscript companion_r.R <package>`: the package's name is
# data, read from the arguments, never code. One `key<TAB>value` line per fact, tabs and line
# breaks in a value folded to a space. A package that does not load prints `error` and exits 3.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) {
  cat("usage: Rscript companion_r.R <package>\n", file = stderr())
  quit(status = 2L)
}
name <- args[[1L]]
fact <- function(key, value) {
  cat(key, "\t", gsub("[\t\r\n]+", " ", value), "\n", sep = "")
}
fact("libpaths", paste(normalizePath(.libPaths(), winslash = "/"), collapse = "|"))
# The namespace itself, loaded as the suite loads it: whatever the library search finds first.
ns <- tryCatch(loadNamespace(name), error = function(e) e)
if (inherits(ns, "error")) {
  fact("error", conditionMessage(ns))
  quit(status = 3L)
}
path <- getNamespaceInfo(ns, "path")
fact("path", normalizePath(path, winslash = "/"))
desc <- read.dcf(file.path(path, "DESCRIPTION"))
for (key in c("Package", "Version", "Hyper-Pin", "Repository", "RemoteType", "Built")) {
  if (key %in% colnames(desc)) fact(key, desc[1L, key])
}
