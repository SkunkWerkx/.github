# R (root): newest release (floats) from CRAN's Debian repo, plus what CI's R legs use.
# CRAN opens a new suite when compiled packages break across an R upgrade (trixie-cran40 stops at 4.5.3, R 4.6
# is in trixie-cran46), so pick the newest trixie-cranNN suite listed rather than hard-coding one.
source /usr/local/share/hyperforge/lib.sh
KEY=95C0FAF38DB3CCAD0C080A7BDC78B2DDEABC47B7
SUITE=$(curl -fsSL https://cloud.r-project.org/bin/linux/debian/ | grep -o 'trixie-cran[0-9]*' | sort -uV | tail -1)
[ -n "$SUITE" ] || { echo "no trixie-cran suite listed"; exit 1; }
export GNUPGHOME=$(mktemp -d)
gpg --batch --keyserver hkps://keyserver.ubuntu.com --recv-keys "$KEY"
gpg --batch --export "$KEY" > /usr/share/keyrings/cran.gpg
rm -rf "$GNUPGHOME"
echo "deb [signed-by=/usr/share/keyrings/cran.gpg] https://cloud.r-project.org/bin/linux/debian $SUITE/" \
  > /etc/apt/sources.list.d/cran.list
apt-get update
# libuv1-dev: the fs package (pulled in by testthat) builds against it; without it testthat silently fails to
# install (install.packages only warns), hence the requireNamespace check below.
apt-get install -y --no-install-recommends -o Acquire::Retries=3 r-base-core r-base-dev libuv1-dev
# rcmdcheck/testthat/lintr: what CI's R legs use. languageserver: the VS Code R extension (reditorsupport.r)
# exits with code 10 without it.
Rscript -e 'install.packages(c("rcmdcheck", "testthat", "lintr", "languageserver"), repos = "https://cloud.r-project.org", Ncpus = parallel::detectCores())'
Rscript -e 'for (p in c("rcmdcheck", "testthat", "lintr", "languageserver")) if (!requireNamespace(p, quietly = TRUE)) stop("missing R package: ", p)'
R --version | head -1
rm -rf /var/lib/apt/lists/* /tmp/Rtmp*
