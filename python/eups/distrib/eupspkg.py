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
    """A class to encapsulate product distribution based on Bourne shell 
    builder scripts structured in RPM-like sections.

    EupsPkg mechanism looks for a file ups/<productname>.pkgbuild, expecting
    it to define the following verbs

       <pkgbuild> fetch     -- fetch the source code for this package
       <pkgbuild> prep      -- prepare the source code (e.g., apply patches)
       <pkgbuild> build     -- configure and build the source
       <pkgbuild> install   -- install the source into its destination

       <pkgbuild> create    -- generate all files needed for distribution

    The fetch, prep, build, and install verbs are used when the package is
    installed via 'eups distrib install'. The create verb is used when the
    package is being created with 'eups distrib create'.

    The results of creating an EupsPkg package are:
        * A manifest in 'manifests/<product>-<version>.manifest'
        * A table file in 'tables/<product>-<version>.manifest'
        * A 'products/<product>-<version>.eupspkg' file. This is a gzipped
          tarball with the following default structure:

          /
           -- pkginfo
           -- pkgbuild

          where pkgbuild is an unmodified copy of ups/<productname>.pkgbuild
          and pkg-info sets variables identifying the package contents, such
          as PRODUCT, VERSION, SOURCES, FLAVOR, etc.

    By overriding the pkgbuild create verb, the user can add or change the
    contents of the package (e.g., include the tarball of the sources,
    instead of just recording the URL where they can be found.

    When the package is being created via 'eups distrib create', roughly the
    following will occur:

        - A temporary directory $pkgdir will be created.
        - Variables PRODUCT, VERSION, FLAVOR, and SOURCES will be stored
          into a file named $pkgdir/pkginfo as key-value pairs
          (Bash-sourcable variable definitions).
        - ups/<productname>.pkgbuild will be copied, unmodified, to 
          '$pkgdir/pkgbuild'
        - 'pkgbuild create' will be called from $pkgdir
             By overriding the 'create' verb, the user can add or modify the
             contents of $pkgdir.
        - $pkgdir will be tarballed and gzipped into a file named 
          <product>-<version>.eupspkg, and copied to $EUPS_PKGROOT/packages

    When the package is being installed by 'eups distrib install', rougly
    the following will occur:

        - eupspkg file will be downloaded and extracted into $pkgdir
        - dependencies will be setup-ed based on the information from the
          manifest file.
        - 'pkgbuild fetch' will be called from $pkgdir.
             The default implementation will download the sources from the
             URL defined in the SOURCES variable (in pkginfo file), and
             unzip them (if needed) into $pkgdir/build
        - 'pkgbuild prep' will be called from $pkgdir.
             The default implementation does nothing.
        - the package will be setup-ed at this point with
             'setup --type=build -j -r .', executed in $pkgdir/build
        - 'pkgbuild build' will be called in $pkgdir.
             The default implementation runs:
               scons opt=3 prefix=$PREFIX version=$VERSION build
             in $pkgdir/build
        - 'pkgbuild install' will be run in $pkgdir.
             The default implementation runs:
               scons opt=3 prefix=$PREFIX version=$VERSION install
             in $pkgdir/build

    There's a default Bash implementation of required eupspkg verbs in
    $EUPS_DIR/lib/eupspkg/functions.  The minimal pkgbuild script is as
    follows:

       #!/bin/bash

       . $EUPS_DIR/lib/eupspkg/functions

       "$@"

    If no pkgbuild script is present in the product's ups/ directory, a
    minimal one will be auto-created and packaged.


    OPTIONS:
    The behavior of a Distrib class is fine-tuned via options (a dictionary
    of named values) that are passed in at construction time.  The options 
    supported are:
       noeups           do not use the local EUPS database for information  
                          while creating packages.       
       obeyGroups       when creating files (other on the user side or the 
                          server side), set group ownership and make group
                          writable
       groupowner       when obeyGroups is true, change the group owner of 
                          to this value
       buildDir         a directory to use to build a package during install.
                          If this is a relative path, the full path will be
                          relative to the product root for the installation.
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

        self.source = self.options.get("source", "")

        # Allow the verbosity of pkgbuild script to be set separately.
        # Useful for debugging.
        # PROBLEM: this only seems to work for 'eups distrib create'
        self.pkgbuild_verbose = self.options.get("verbose", Eups.verbose)


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

        (baseDir, productDir) = self.getProductInstDir(product, version, flavor)
        pkgbuild = os.path.join(baseDir, productDir, "ups", "pkgbuild")
        if not os.path.exists(pkgbuild):
            # Use the defalt build file
            raise Exception("TODO: Implement default pkgbuild file facility.")

        # Construct the package in a temporary directory
        pkgdir = tempfile.mkdtemp(suffix='.eupspkg')

        q = pipes.quote
        try:
            # Execute 'pkgbuild <create>'
            cmd = ("cd %(pkgdir)s && " + \
                "VERBOSE=%(verbose)s PRODUCT=%(product)s VERSION=%(version)s FLAVOR=%(flavor)s SOURCE=%(source)s " + \
                "%(pkgbuild)s create") % \
                    {
                      'pkgdir':   q(pkgdir),
                      'verbose':  q(self.pkgbuild_verbose),
                      'product':  q(product),
                      'version':  q(version),
                      'flavor':   q(flavor),
                      'pkgbuild': q(pkgbuild),
                      'source':   q(self.source),
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
                    tf = tarfile.open(tfn, mode='w:gz')
                    tf.add(pkgdir, arcname="%s-%s" % (product, version))
                    tf.close()
                except IOError, param:
                    try:
                        os.unlink(tfn)
                    except OSError:
                        pass                        # probably didn't exist
                    raise RuntimeError ("Failed to write %s: %s" % (tfn, param))
        finally:
            shutil.rmtree(pkgdir)

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
        pkgdir  = os.path.join(buildDir, "%s-%s" % (product, version)) # we expect this directory in the eupspkg tarball

        # Determine temporary build directory
        if not buildDir:
            buildDir = self.getOption('buildDir', 'EupsBuildDir')
        if self.verbose > 0:
            print >> self.log, "Building package: %s" % pkg
            print >> self.log, "Building in directory:", buildDir
            print >> self.log, "Writing log to: %s" % (logfile)

        q = pipes.quote
        try:
            buildscript = os.path.join(buildDir, "build.sh")
            fp = open(buildscript, 'a')
            try:
                fp.write("""\
#!/bin/bash
# ----
# ---- This script has been autogenerated by 'eups distrib install'.
# ----

VERB=%(verbose)s

set -x
set -e
cd %(buildDir)s

# Unpack the eupspkg tarball
tar xzvf %(eupspkg)s
cd %(pkgdir)s

# setup the required packages
%(setups)s

# fetch package source
( VERBOSE=$VERB ./ups/pkgbuild fetch ) || exit -1

# prepare for build (e.g., apply platform-specific patches)
( VERBOSE=$VERB ./ups/pkgbuild prep  ) || exit -2

# setup
setup --type=build -j -r .

# build and install
( VERBOSE=$VERB ./ups/pkgbuild build   ) || exit -3
( VERBOSE=$VERB ./ups/pkgbuild install ) || exit -4
""" 			% {
                        'verbose' : self.pkgbuild_verbose,
                        'buildDir' : q(buildDir),
                        'eupspkg' : q(tfname),
                        'pkgdir' : q(pkgdir),
                        'setups' : "\n".join(setups),
                        'product' : q(product),
                        'version' : q(version),
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
                    eupsServer.system("tail -20 %s 1>&2" % logfile)
                except:
                    pass
            raise RuntimeError("Failed to build %s: %s" % (pkg, str(e)))

        if self.verbose > 0:
            print >> self.log, "Install for %s successfully completed" % pkg
