#!/usr/bin/env python
# -*- python -*-
#
# Export a product and its dependencies as a package, or install a
# product from a package: a specialization for the "EupsPkg" mechanism
#
import sys, os, shutil, tarfile, tempfile, pipes, stat
import eups
import Distrib as eupsDistrib
import server as eupsServer
import eups.hooks

class Distrib(eupsDistrib.DefaultDistrib):
    """A class to implement product distribution based on packages
    ("EupsPkg packages") constructed by builder scripts implementing 
    verbs not unlike RPM's %xxxx macros.

    EupsPkg packages are free-formed gzipped tarballs ending in .eupspkg. 
    When an EupsPkg package is being installed via 'eups distrib install',
    the tarball ie expanded into a temporary directory (hereafter called
    $pkgdir) and a file ./ups/pkgbuild is searched for.  This file is
    expected to be executable, and define the following verbs:

       pkgbuild fetch     -- fetch the source code for this package
       pkgbuild prep      -- prepare the source code (e.g., apply patches)
       pkgbuild config    -- configure the source code
       pkgbuild build     -- build the source code
       pkgbuild install   -- install the binary to its destination

    These are invoked by EUPS, in sequence, from $pkgdir. For all verbs, the
    dependencies, as obtained from the manifest, will be setup-ed. 
    Additionally, for 'config', 'build', and 'install', the product itself
    will be setup-ed (i.e., `setup --type=build -k -r .' will be executed in
    $pkgdir).  All invocations are run from an auto-generated Bash script
    named $pkgdir/../build.sh; in case of build problems, the end-user can
    inspect and edit it (as well as ups/pkgbuild) as necessary.

    The pkgbuild script should not need to manipulate the EUPS environment,
    nor declare the product to EUPS upon successful completion of
    installation.  Note that this is different from the custom for EUPS'
    .build distribution mechanism.
    
    If the packager does not provide a pkgbuild script, a default one will
    be used that provides implementations of all verbs for common build
    systems (see below).


    When a package is being created using 'eups distrib create', the create
    verb will be invoked on the installed product's pkgbuild script:

       pkgbuild create    -- put together files needed for distribution

    The script will be invoked from an empty temporary directory (hereafter,
    $pkgdir), and is expected to copy or otherwise generate all files
    necessary for the package (including ./ups/pkgbuild, which is,
    presumably, just a copy of itself).
    
    The information about the product being packaged will be passed via
    environment variables. Currently, the following ones are defined:
    
       $PRODUCT	  -- product name, as given to eups distrib create
       $VERSION   -- product version, as given to eups distrib create
       $FLAVOR    -- product flavor, as given to eups distrib create

       $SOURCE    -- pkgbuild-specific mechanism to retrieve the 
                     product source (passed via -S option, see below)
       $VERBOSE   -- pkgbuild-specific verbosity level (passed via -S
                     option, see below)
    
    Once 'pkgbuild create' returns, the $pkgbuild directory is tarballed and
    stored to serverDir with .eupspkg extension. Metadata (the manifest as
    well as the table file) is stored as well.

    EupsPkg distribution servers have the following structure:
    
       /+-- config.txt	-- distribution server configuration
        |-- products    -- directory with .eupspkg packages
        |-- tables      -- directory of .table files, one per package
        \-- manifests   -- directory of manifests, one per package

    Standard .list files are used to capture tag information.


    The above requirements on verbs present are all that's required of
    pkgbuild scripts.  The creation and building of packages, as well as
    interpretation of $SOURCE and $VERBOSE inputs is completely under the
    pkgbuild script's control.  EUPS has no awareness of package contents,
    beyond assuming 'ups/pkgbuild' is the ``entry point'' for creation and
    installs.
    
    This allows for high degree of customization, as the pkgbuild script
    that the packager writes is free to internally organize the package in
    arbitrary ways, or implement different ways of getting to the source
    (e.g., include it in the package, or just keep a pointer to a location
    on the web, or a location in a version control system).  Also, note that
    there's no requirement that pkgbuild scripts are "scripts" (eg, Bash),
    as long as they're executable on the end-users system (they could be
    Python programs).
    
    In practice, the range of build systems commonly in use is (fortunately)
    limited, most adhering to widely accepted conventions (eg.,
    "./configure"/"make"/"make install" idioms for autoconf, etc.). EupsPkg
    provides a default (Bash) library of pkgbuild verb implementations, to
    greatly simplify the writing of pkgbuild scripts.

    The default (Bash) verb implementation library can be found in:
    
       $EUPS_DIR/lib/eupspkg/functions

    and is guaranteed to be present on any system running EUPS with EupsPkg. 
    A typical pkgbuild script using these will look as follows:
    
       ================================================================
       #!/bin/bash
    
       . "$EUPS_DIR/lib/eupspkg/functions"
    
       "$@"
       ================================================================

    The script above sources the function library, and ends with "$@", which
    will execute the verb passed in on the command line.  The default verb
    implementations will use $REPOSITORY_PATH, as well as other variables passed
    in by EUPS via the environment (discussed above) to create or install
    the package (depending on which one is invoked).
    
    The code above is the default implementation of pkgbuild that EUPS will
    use unless the packager provides their own.  This enables completely
    non-intrusive builds of eupspkg packages for repositories using standard
    build systems/options.

    
    $REPOSITORY_PATH is a '|'-delimited path of patterns expanding to
    repositories where the source may be found.  It should be specified via
    a -S option to 'eups distrib create'.  An example of a typical
    $REPOSITORY_PATH specification is as follows:
 
       eups distrib create .... \
         -S repository_path='git://server1/dir1/$PRODUCT|git://server2/dir2/$PRODUCT'


    Note how elements of the path are separated by | (instead of the usual
    colon). Secondly, note how the path has been enclosed in single quotes,
    to prevent variable expansion on the command line. Finally, although
    we've written it in lower case, the names of the variables passed in via
    -S will be converted to upper case before being passed on to pkgbuild.

    EUPS will construct a repository URL from each element of the path, and
    test for its existence until a matching one is found.  Instead of using
    matching via $REPOSITORY_PATH, the repository URL can be embedded into
    the pkgbuild file itself by setting a variable named REPOSITORY.


    In the context of package creation, the default create verb
    implementation interprets the $SOURCE variable as the mechanism through
    which the source code will be obtained when the package is installed. 
    The following mechanisms are defined:
    
       git-archive  -- use 'git archive' to fetch the source. The $VERSION
                       will be interpreted[*] as a named git ref (tag or
                       branch name) to be checked out.  Note that
                       git-archive can't be used to fetch the source by SHA1
                       or by the result of git describe; a true named ref
                       must be used.
       git          -- use 'git clone' to fetch the source. The $VERSION
                       is interpreted[*] as for git-archive, but any ref
                       parseable by git will work. Note that this is less
                       efficient since the whole git repository needs to be
                       checked out.
       local        -- the source is included in the package. This is
                       optimal from the user's point of view, since it
                       removes dependencies on git executable or repository
                       to install the package.  Note that git is still used
                       to obtain the source in the 'eups distrib create'
                       phase.

       [*] footnote: there is some minimal parsing of $VERSION, such as
           removal of +XXX prefixes (if any), to attempt to convert it to a
           valid git ref. See version_to_git_rev() function for details.

    To control package creation, EupsPkg allows the following variables to
    be passed to 'eups distrib create' via '-S' switch:
    
       source     -- define the content of $SOURCE to be passed to 
                     'pkgbuild create' (default: "")
       verbose    -- set $VERBOSE, to be passed to 'pkgbuild create'
                     (default: same as EUPS verbosity level)

    The ability to define 'source' at package creation time is quite
    powerfull; eg., it allows one to easily switch from remote git-archive
    to local source storage, or mix-and-match different mechanisms to
    different products (eg., if a product contains gigabytes of test data,
    it may be better to keep them in a git repository, than have potentially
    hundreds of tarballed copies on the distribution server).

    A typical invocation of 'eups distrib create' using the built-in verb
    implementations is therefore:

       eups distrib create base 7.3.1.1_2_g3dd8623 \
          --server-dir=...serverDir... -f generic -d eupspkg \
          -S source=git -S repository_path=....

    If '-S source' was not given, 'local' would be the default.
    
    The default create verb implementation uses the information from the
    command line to construct the package.  It saves any information needed
    to later build it (e.g., the $SHA1, or the resolved $REPOSITORY) to
    ./ups/pkginfo in the package itself.  To restore it, this file is
    sourced by pkgbuild at 'eups distrib install' time.
    

    On install, the default verb implementations will try to detect the
    build system (in the order given below), and handle it as follows:
    
       scons       -- if 'SConstruct' exists in package root, assume the
                      build system is scons. Run 'scons opt=3 prefix=$PREFIX
                      version=$VERSION' to build.
       autoconf    -- if 'configure' exists in package root, assume the
                      build system is autoconf. Run ./configure in config
                      verb, make in build(), and make install in install().
       make        -- if 'Makefile' exists in package root, assume the build
                      is driven by simple makefiles. Run 'make
                      prefix=$PREFIX' in build() and 'make prefix=$PREFIX
                      install' to install.
       distutils   -- if 'setup.py' exists in package root. Run 'python
                      setup.py' to build/install.
       <default>   -- if no other build system is detected, assume there's
                      nothing to build. Simply copy the source directory to
                      $PREFIX to install.

    Note that the default install() verb will copy the ups/ directory to the
    destination directory, and expand the table file using 'eups
    expandtable'.  Default implementation of prep() does nothing.  For
    details see the implementations of these and other verbs in the function
    library().


    There are two ways of custimizing the pkgbuild scripts that use the
    standard library: setting variables, or overriding defined functions.
    Unless the build process is complex, overriding the variables is usually
    sufficient to achieve the desired customization.
    
    For the full list of variables that can be overridden, see the bottom of
    the .../lib/eupspkg/functions file. Here we list a few of the more
    commonly used ones:
    
       $REPOSITORY              -- The URL to git repository with the
                                   source. Can use any protocol git
                                   understands (eg. git://, http://, etc.).
                                   If not specified, $REPOSITORY_PATH will
                                   be searched for a match (and this is the
                                   recommended usage).
       $CONFIGURE_OPTIONS       -- Options to be passed to ./configure (if
                                   autoconf is in used). Default:
                                   --prefix=$PREFIX
       $MAKE_BUILD_TARGETS      -- Targets to make in build step (if 
                                   Makefiles are in use). Not set by
                                   default.
       $MAKE_INSTALL_TARGETS    -- Targets to pass to make in install step.
                                   Default: install.
       $PYSETUP_INSTALL_OPTIONS -- Options to pass to setup.py in install
                                   step. Default: --prefix $PREFIX.

    The verbs themselves can also be overridden. For example, the pkgbuild
    file for Boost C++ library overrides the config verb as follows:
    
       ================================================================
       config()
       {
           detect_compiler
       
           if [[ "$COMPILER_TYPE" == clang ]]; then
               WITH_TOOLSET="--with-toolset clang"
           fi

           ./bootstrap.sh --prefix="$PREFIX" $WITH_TOOLSET
       }
       ================================================================

    This configures the Boost build system and passes it the correct toolset
    optins if running with the clang compiler.  detect_compiler() is a
    utility function present in the library, defining $COMPILER_TYPE based
    on the detected compiler.  See the source code of the library for the
    list of available functions and their typical usage.

    There are many other (undocumented) subroutines and options that are
    present in the function library, including utilities and command line
    switches to help debugging. Browse through the library code to get a
    feel for it.


    Debugging/Development support

    To help with development of pkgbuild scripts, additional verbs are
    provided by the default pkgbuild implementation:
    
       xcreate  -- create the package contents and place it into ./_create.
                   Must be invoked from the root product directory.
       xfetch   -- run 'fetch' on the package contents found in ./_create.
                   The output is stored in ./_fetch
       xclean   -- remove _create and _fetch directories.

    The following options to pkgbuild are provided as well:
    
       -a             -- auto-detect the product name and version, using git.
                         The version will be a slightly mangled output of
                         git-describe --always --dirty.
       -d             -- do not run git-describe with --dirty when
                         autodetecting the version with -a.
       -v <verbosity> -- set verbosity level
       -h             -- get help on available options.

    As a convenience, the default implementation of
    $EUPS_DIR/lib/eupspkg/functions will eval any arguments passed after the
    verb, enabling constructs such as:
    
       ./ups/pkgbuild xcreate PRODUCT=... VERSION=...
       
    vs. 'env PRODUCT=... VERSION=... ./ups/pkgbuild xcreate'. Beware of
    quoting issues when using this feature (eg.  pkgbuild xcreate
    REPOSITORY_PATH='.../$PRODUCT.git|.../$PRODUCT.git' will not do what you
    think it would, as the quotes will be expanded on the command line,
    leaving pkgbuild to believe it's executing a pipe.

    Finally, there's a script named 'pkgbuild' in $EUPS_DIR/bin (and,
    therefore, on $PATH whenever eups is setup). It's a small wrapper that
    dispatches the calls to ./ups/pkgbuild, if it exists, and to the default
    EUPS-provided implementation otherwise. It allows the developer the
    conveninece to write:
    
       pkgbuild -a xcreate
       
    in the root product directory and be confident it will work irrespective
    of whether ./ups/pkgbuild or the default implementation is being used.


    Further Examples:
    
       To be contributed.

    """

    NAME = "eupspkg"
    PRUNE = True

    def __init__(self, Eups, distServ, flavor, tag="current", options=None,
                 verbosity=0, log=sys.stderr):
        eupsDistrib.Distrib.__init__(self, Eups, distServ, flavor, tag, options,
                                     verbosity, log)

        self._msgs = {}

        self.nobuild = self.options.get("nobuild", False)
        self.noclean = self.options.get("noclean", False)

        # Allow the verbosity of pkgbuild script to be set separately.
	if "verbose" not in self.options:
		self.options["verbose"] = str(Eups.verbose)

	# Prepare the string with all unrecognized options, to be passed to pkgbuild on the command line
	# FIXME: This is not the right way to do it. -S options should be preserved in a separate dict()
	knownopts = set(['config', 'nobuild', 'noclean', 'noaction', 'exact', 'allowIncomplete', 'buildDir', 'noeups', 'installCurrent']);
        self.qopts = " ".join( "%s=%s" % (k.upper(), pipes.quote(str(v))) for k, v in self.options.iteritems() if k not in knownopts )

    # @staticmethod   # requires python 2.4
    def parseDistID(distID):
        """Return a valid package location if and only we recognize the 
        given distribution identifier

        This implementation return a location if it starts with "eupspkg:"
        """
        if distID:
            prefix = "eupspkg:"
            distID = distID.strip()
            if distID.startswith(prefix):
                return distID[len(prefix):]

        return None

    parseDistID = staticmethod(parseDistID)  # should work as of python 2.2

    def initServerTree(self, serverDir):
        """initialize the given directory to serve as a package distribution
        tree.
        @param serverDir    the directory to initialize
        """
        eupsDistrib.DefaultDistrib.initServerTree(self, serverDir)

        config = os.path.join(serverDir, eupsServer.serverConfigFilename)
        if not os.path.exists(config):
            configcontents = """\
# Configuration for a EupsPkg-based server
EUPSPKG_URL = %(base)s/products/%(path)s
"""
            cf = open(config, 'a')
            try:
                cf.write(configcontents)
            finally:
                cf.close()


    def getManifestPath(self, serverDir, product, version, flavor=None):
        """return the path where the manifest for a particular product will
        be deployed on the server.  In this implementation, all manifest 
        files are deployed into a subdirectory of serverDir called "manifests"
        with the filename form of "<product>-<version>.manifest".  Since 
        this implementation produces generic distributions, the flavor 
        parameter is ignored.

        @param serverDir      the local directory representing the root of 
                                 the package distribution tree.  In this 
                                 implementation, the returned path will 
                                 start with this directory.
        @param product        the name of the product that the manifest is 
                                for
        @param version        the name of the product version
        @param flavor         the flavor of the target platform for the 
                                manifest.  This implementation ignores
                                this parameter.
        """
        return os.path.join(serverDir, "manifests", 
                            "%s-%s.manifest" % (product, version))

    def createPackage(self, serverDir, product, version, flavor=None, overwrite=False):
        """Write a package distribution into server directory tree and 
        return the distribution ID 
        @param serverDir      a local directory representing the root of the 
                                  package distribution tree
        @param product        the name of the product to create the package 
                                distribution for
        @param version        the name of the product version
        @param flavor         the flavor of the target platform; this may 
                                be ignored by the implentation
        @param overwrite      if True, this package will overwrite any 
                                previously existing distribution files even if Eups.force is false
        """
        distid = self.getDistIdForPackage(product, version)
        distid = "eupspkg:%s-%s.eupspkg" % (product, version)

        # Make sure it's an absolute path
        serverDir = os.path.abspath(serverDir)

        (baseDir, productDir) = self.getProductInstDir(product, version, flavor)
        pkgbuild = os.path.join(baseDir, productDir, "ups", "pkgbuild")
        if not os.path.exists(pkgbuild):
            # Use the defalt build file
            pkgbuild = os.path.join(os.environ["EUPS_DIR"], 'lib', 'eupspkg', 'pkgbuild.default')

        # Construct the package in a temporary directory
        pkgdir0 = tempfile.mkdtemp(suffix='.eupspkg')
        prodSubdir = "%s-%s" % (product, version)
        pkgdir = os.path.join(pkgdir0, prodSubdir)
        os.mkdir(pkgdir)

        q = pipes.quote
        try:
            # Execute 'pkgbuild <create>'
            cmd = ("cd %(pkgdir)s && " + \
                "%(pkgbuild)s   PREFIX=%(prefix)s PRODUCT=%(product)s VERSION=%(version)s FLAVOR=%(flavor)s %(qopts)s" + \
                " create") % \
                    {
                      'pkgdir':   q(pkgdir),
                      'prefix':   q(os.path.join(baseDir, productDir)),
                      'product':  q(product),
                      'version':  q(version),
                      'flavor':   q(flavor),
                      'pkgbuild': q(pkgbuild),
                      'qopts':    self.qopts,
                    }
            eupsServer.system(cmd)

            # Tarball the result and copy it to $serverDir/products
            productsDir = os.path.join(serverDir, "products")
            if not os.path.isdir(productsDir):
                try:
                    os.makedirs(productsDir)
                except:
                    raise RuntimeError, ("Failed to create %s" % (productsDir))

            tfn = os.path.join(productsDir, "%s-%s.eupspkg" % (product, version))
            if os.path.exists(tfn) and not (overwrite or self.Eups.force):
                if self.Eups.verbose > 1:
                    print >> self.log, "Not recreating", tfn
                return distid

            if not self.Eups.noaction:
                if self.verbose > 1:
                    print >> self.log, "Writing", tfn

                try:
                    cmd = 'cd %s && tar czf %s %s' % (q(pkgdir0), q(tfn), q(prodSubdir))
                    eupsServer.system(cmd)
                except OSError, e:
                    try:
                        os.unlink(tfn)
                    except OSError:
                        pass                        # probably didn't exist
                    raise RuntimeError ("Failed to write %s: %s" % (tfn, e))
        finally:
            shutil.rmtree(pkgdir0)

        return distid

    def getDistIdForPackage(self, product, version, flavor=None):
        """return the distribution ID that for a package distribution created
        by this Distrib class (via createPackage())
        @param product        the name of the product to create the package 
                                distribution for
        @param version        the name of the product version
        @param flavor         the flavor of the target platform; this may 
                                be ignored by the implentation.  None means
                                that a non-flavor-specific ID is preferred, 
                                if supported.
        """
        return "eupspkg:%s-%s.eupspkg" % (product, version)

    def packageCreated(self, serverDir, product, version, flavor=None):
        """return True if a distribution package for a given product has 
        apparently been deployed into the given server directory.  
        @param serverDir      a local directory representing the root of the 
                                  package distribution tree
        @param product        the name of the product to create the package 
                                distribution for
        @param version        the name of the product version
        @param flavor         the flavor of the target platform; this may 
                                be ignored by the implentation.  None means
                                that the status of a non-flavor-specific package
                                is of interest, if supported.
        """
        location = self.parseDistID(self.getDistIdForPackage(product, version, flavor))
        return os.path.exists(os.path.join(serverDir, "products", location))

    def installPackage(self, location, product, version, productRoot, 
                       installDir, setups=None, buildDir=None):
        """Install a package with a given server location into a given
        product directory tree.
        @param location     the location of the package on the server.  This 
                               value is a distribution ID (distID) that has
                               been stripped of its build type prefix.
        @param productRoot  the product directory tree under which the 
                               product should be installed
        @param installDir   the preferred sub-directory under the productRoot
                               to install the directory.  This value, which 
                               should be a relative path name, may be
                               ignored or over-ridden by the pacman scripts
        @param setups       a list of EUPS setup commands that should be run
                               to properly build this package.  This is usually
                               ignored by the pacman scripts.
        """

        pkg = location
        tfname = self.distServer.getFileForProduct(pkg, product, version,
                                                   self.Eups.flavor,
                                                   ftype="eupspkg", 
                                                   noaction=self.Eups.noaction)

        logfile = os.path.join(buildDir, "build.log") # we'll log the build to this file

        # Determine temporary build directory
        if not buildDir:
            buildDir = self.getOption('buildDir', 'EupsBuildDir')
        if self.verbose > 0:
            print >> self.log, "Building package: %s" % pkg
            print >> self.log, "Building in directory:", buildDir
            print >> self.log, "Writing log to: %s" % (logfile)

        # Make sure the buildDir is empty (to avoid interference from failed builds)
        shutil.rmtree(buildDir)
        os.mkdir(buildDir)

	# Construct the build script
        q = pipes.quote
        try:
            buildscript = os.path.join(buildDir, "build.sh")
            fp = open(buildscript, 'w')
            try:
                fp.write("""\
#!/bin/bash
# ----
# ---- This script has been autogenerated by 'eups distrib install'.
# ----

set -xe
cd %(buildDir)s

# sanitize the environment: unsetup any packages that were setup-ed
for pkg in $(eups list -s | cut -d' ' -f 1); do
	unsetup "$pkg"
done

# Unpack the eupspkg tarball
tar xzvf %(eupspkg)s

# Enter the directory unpacked from the tarball
PKGDIR="$(find . -maxdepth 1 -type d ! -name ".*" | head -n 1)"
test ! -z $PKGDIR
cd "$PKGDIR"

# If ./ups/pkgbuild is not present, symlink in the default
if [[ ! -e ./ups/pkgbuild ]]; then
	mkdir -p ./ups
	ln -s "$EUPS_DIR/lib/eupspkg/pkgbuild.default" ups/pkgbuild
fi

# eups setup the dependencies
%(setups)s

# fetch package source
( ./ups/pkgbuild %(qopts)s fetch ) || exit -1

# prepare for build (eg., apply platform-specific patches)
( ./ups/pkgbuild %(qopts)s prep  ) || exit -2

# setup the package being built. note we're using -k
# to ensure setup-ed dependencies aren't overridden by
# the table file. we could've used -j instead, but then
# 'eups distrib install -j ...' installs would fail as 
# these don't traverse and setup the dependencies.
setup --type=build -k -r .

# configure, build, and install
( ./ups/pkgbuild %(qopts)s config  ) || exit -3
( ./ups/pkgbuild %(qopts)s build   ) || exit -4
( ./ups/pkgbuild %(qopts)s install ) || exit -5
""" 			% {
                        'buildDir' : q(buildDir),
                        'eupspkg' : q(tfname),
                        'setups' : "\n".join(setups),
                        'product' : q(product),
                        'version' : q(version),
                        'qopts' : self.qopts,
                      }
                )
            finally:
                fp.close()

            # Make executable (equivalent of 'chmod +x $buildscript')
            st = os.stat(buildscript)
            os.chmod(buildscript, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            #
            # Did they ask to have group permissions honoured?
            #
            self.setGroupPerms(buildDir + "*")

            # Run the build
            cmd = "(%s) >> %s 2>&1 " % (q(buildscript), q(logfile))
            if not self.nobuild:
                eupsServer.system(cmd, self.Eups.noaction)

                # Copy the build log into the product install directory. It's useful to keep around.
                installDirUps = os.path.join(self.Eups.path[0], self.Eups.flavor, product, version, 'ups')
                if os.path.isdir(installDirUps):
                    shutil.copy2(logfile, installDirUps)
                    print >> self.log, "Build log file copied to %s/%s" % (installDirUps, os.path.basename(logfile))
                else:
                    print >> self.log, "Build log file not copied as %s does not exist (this shouldn't happen)." % installDirUps

        except OSError, e:
            if self.verbose >= 0 and os.path.exists(logfile):
                try: 
                    print >> self.log, "BUILD ERROR!  From build log:"
                    eupsServer.system("tail -20 %s 1>&2" % q(logfile))
                except:
                    pass
            raise RuntimeError("Failed to build %s: %s" % (pkg, str(e)))

        if self.verbose > 0:
            print >> self.log, "Install for %s successfully completed" % pkg
