#!/usr/bin/env python
# -*- python -*-
#
# Export a product and its dependencies as a package, or install a
# product from a package: a specialization for the "Builder" mechanism
#
import sys, os, re, atexit, shutil
import eups
import Distrib as eupsDistrib
import server as eupsServer
import eups.hooks

class Distrib(eupsDistrib.DefaultDistrib):
    """A class to encapsulate product distribution based on Bourne shell 
    builder scripts

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
       allowIncomplete  if True, do not stop if we can't create a buildfile
                          for a specific package.  
       buildDir         a directory to use to build a package during install.
                          If this is a relative path, the full path will be
                          relative to the product root for the installation.
       buildFilePath    a colon-delimited set of directories to look for 
                          build file templates in.
    """

    NAME = "builder"
    PRUNE = True

    def __init__(self, Eups, distServ, flavor, tag="current", options=None,
                 verbosity=0, log=sys.stderr):
        eupsDistrib.Distrib.__init__(self, Eups, distServ, flavor, tag, options,
                                     verbosity, log)

        self._msgs = {}

        self.allowIncomplete = False
        if self.options.has_key('allowIncomplete'):
            self.allowIncomplete = self.options['allowIncomplete']

        self.buildFilePath = ""
        if self.options.has_key('buildFilePath'):
            self.buildFilePath = self.options['buildFilePath']

        self.nobuild = self.options.get("nobuild", False)
        self.noclean = self.options.get("noclean", False)

        self.svnroot = ""
        if self.options.has_key('svnroot'):
            self.svnroot = self.options['svnroot']

        self.cvsroot = ""
        if self.options.has_key('cvsroot'):
            self.cvsroot = self.options['cvsroot']

        

    # @staticmethod   # requires python 2.4
    def parseDistID(distID):
        """Return a valid package location if and only we recognize the 
        given distribution identifier

        This implementation return a location if it starts with "pacman:"
        """
        if distID:
            prefix = "build:"
            distID = distID.strip()
            if distID.startswith(prefix):
                return distID[len(prefix):]

        return None

    parseDistID = staticmethod(parseDistID)  # should work as of python 2.2

    def checkInit(self, forserver=True):
        """Check that self is properly initialised; this matters for subclasses 
        with special needs"""
        okay = True
        if not eupsDistrib.Distrib.checkInit(self, forserver):
            okay = False

        try:
            type(self.buildDir)
        except AttributeError, e:
            self.buildDir = None
            print >> self.log, "Incorrectly initialised eupsDistribBuilder: %s" % e
            okay = False

        if forserver:
            try:
                type(self.buildFilePath)
            except AttributeError, e:
                self.buildFilePath = None
                print >> self.log, "Incorrectly initialised eupsDistribBuilder: %s" % e
                okay = False

        return okay

    def initServerTree(self, serverDir):
        """initialize the given directory to serve as a package distribution
        tree.
        @param serverDir    the directory to initialize
        """
        eupsDistrib.DefaultDistrib.initServerTree(self, serverDir)

        config = os.path.join(serverDir, eupsServer.serverConfigFilename)
        if not os.path.exists(config):
            if self.flavor == "generic":
                flavorPath = ""
            else:
                flavorPath = "%(flavor)s/"

            configcontents = """# Configuration for a Builder-based server
MANIFEST_URL = %%(base)s/manifests/%s%%(product)s-%%(version)s.manifest
BUILD_URL = %%(base)s/builds/%%(path)s
DIST_URL = %%(base)s/builds/%%(path)s
""" % (flavorPath)
            
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

    def createPackage(self, serverDir, product, version, flavor=None,
                      overwrite=False, letterVersion=None):
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
        @param letterVersion  the name for the desired "letter version"; a version
                                 following a rebuild caused by an ABI change in a dependency
        """
        productName = product
        versionName = version
        letterVersionName = letterVersion

        (baseDir, productDir) = self.getProductInstDir(productName, versionName, flavor)

        builder = "%s-%s.build" % (productName, letterVersionName)
        buildFile = self.find_file_on_path("%s.build" % productName, os.path.join(baseDir, productDir, "ups"))

        if not buildFile:               # try a template buildfile from some .eups directory
            path = self.Eups.path[:]; path.reverse() # the first elements on EUPS_PATH have highest priority
            files = eups.hooks.loadCustomization(verbose=0, path=path,
                                                 filename="template.build", execute=False)
            if files:
                buildFile = files[0]

                if self.verbose:
                    print >> self.log, "Using %s to build %s %s" % (buildFile, productName, versionName)

        if not buildFile:
            msg = "I can't find a build file %s.build for version %s anywhere on builder path \"%s\"" % \
                  (productName, versionName, self.buildFilePath)
            if self.allowIncomplete:
                msg += "; proceeding anyway"

            bpath = []
            if self.buildFilePath:
                bpath += self.buildFilePath.split(":")
            bpath += ["."]

            for d in bpath:
                if os.path.exists(os.path.join(d, "ups", "%s.build" % productName)):
                    msg += "\n" + "N.b. found %s.build in %s/ups; consider adding %s/ups to --build path" % \
                           (d, d, productName)

            if self.verbose > 1 or not self._msgs.has_key(msg):
                self._msgs[msg] = 1
                print >> self.log, msg
            if self.allowIncomplete:
                return None

            raise RuntimeError("I'm giving up. Use --incomplete if you want to proceed with a partial distribution")

        builderDir = os.path.join(serverDir, "builds")
        if not os.path.isdir(builderDir):
            try:
                os.makedirs(builderDir)
            except:
                raise RuntimeError, ("Failed to create %s" % (builderDir))

        full_builder = os.path.join(builderDir, builder)
        if os.access(full_builder, os.R_OK) and not (overwrite or self.Eups.force):
            if self.Eups.verbose > 1:
                print >> self.log, "Not recreating", full_builder
            return "build:" + builder

        if self.verbose > 1:
            print >> self.log, "Writing", full_builder

        try:
            if not self.Eups.noaction:
                if False:
                    eupsServer.copyfile(buildFile, full_builder)
                else:
                    try:
                        ifd = open(buildFile)
                    except IOError, e:
                        raise RuntimeError, ("Failed to open file \"%s\" for read" % buildFile)
                    try:
                        ofd = open(full_builder, "w")
                    except IOError, e:
                        raise RuntimeError, ("Failed to open file \"%s\" for write" % full_builder)

                    try:
                        builderVars = eups.hooks.config.distrib["builder"]["variables"]
                        # Grandfather in {CVS,SVN}ROOT from the command line
                        if self.cvsroot:
                            builderVars["CVSROOT"] = self.cvsroot
                        if self.svnroot:
                            builderVars["SVNROOT"] = self.svnroot

                        expandBuildFile(ofd, ifd, productName, letterVersionName, self.verbose, builderVars,
                                        repoVersionName=versionName)
                    except RuntimeError, e:
                        raise RuntimeError, ("Failed to expand build file \"%s\": %s" % (full_builder, e))

                    del ifd; del ofd
        except IOError, param:
            try:
                os.unlink(full_builder)
            except OSError:
                pass                        # probably didn't exist
            raise RuntimeError ("Failed to write %s: %s" % (full_builder, param))

        return "build:" + builder

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
        return "build:%s-%s.build" % (product, version)

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
        location = self.parseDistID(self.getDistIdForPackage(product, version, 
                                                             flavor))
        return os.path.exists(os.path.join(serverDir, "builds", location))

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

        builder = location
        tfile = self.distServer.getFileForProduct(builder, product, version,
                                                  self.Eups.flavor, 
                                                  ftype="build", 
                                                  noaction=self.Eups.noaction)

        if False:
            if not self.Eups.noaction and not os.access(tfile, os.R_OK):
                raise RuntimeError, ("Unable to read %s" % (tfile))

        if not buildDir:
            buildDir = self.getOption('buildDir', 'EupsBuildDir')
        if self.verbose > 0:
            print >> self.log, "Building in", buildDir

        logfile = os.path.join(buildDir, builder + ".log") # we'll log the build to this file

        if self.verbose > 0:
            print >> self.log, "Executing %s in %s" % (builder, buildDir)
            print >> self.log, "Writing log to %s" % (logfile)
        #
        # Prepare to actually do some work
        #
        cmd = ["cd %s && " % buildDir]
        if setups is not None:
            cmd += map(lambda x: x + " && ", setups)

        if self.verbose > 2:
            cmd += ["set -x &&"]
            if self.verbose > 3:
                cmd += ["set -v &&"]
        #
        # Rewrite build file to replace any setup commands by "setup --just" as we're setting things up
        # explicitly and a straight setup in the build file file undo our hard work
        #
        lines = []                      # processed command lines from build file
        if self.nobuild:
            lines.append("setup --just --type=build -r .")
        else:
            try:
                fd = open(tfile)
            except IOError, e:
                raise RuntimeError, ("Failed to open %s: %s" % (tfile, e))

            for line in fd:
                line = re.sub(r"\n$", "", line) # strip newline

                if re.search("^#!/bin/(ba|k)?sh", line):      # a #!/bin/sh line; not needed
                    continue

                line = re.sub(r"^\s*setup\s", "setup --keep --type=build ", line)
                if not re.search(r"^\s*(\#.*)?$", line): # don't confuse the test for an empty build file ("not lines")
                    lines += [line]
            del fd

        if not lines:
            lines += [":"]              # we need at least one command as cmd may end &&
        cmd += lines
        #
        # Did they ask to have group permissions honoured?
        #
        self.setGroupPerms(os.path.splitext(builder)[0] + "*")

        #
        # Write modified (== as run) build file to self.buildDir
        #
        bfile = os.path.join(buildDir, builder)
        if eupsServer.issamefile(bfile, tfile):
            print >> self.log, "%s and %s are the same; not adding setups to installed build file" % \
                  (bfile, tfile)
        else:
            try:
                bfd = open(bfile, "w")
                for line in cmd:
                    print >> bfd, line
                del bfd
            except Exception, e:
                os.unlink(bfile)
                raise RuntimeError, ("Failed to write %s" % bfile)

        if self.verbose and not self.nobuild:
            print "Issuing commands:"
            print "\t", str.join("\n\t", cmd)

        print >> file(logfile, "w"), str.join("\n\t", cmd)

        if False:
            cmd = "(%s) 2>&1 | tee >> %s" % (str.join("\n", cmd), logfile)
        else:
            cmd = "(%s) >> %s 2>&1 " % (str.join("\n", cmd), logfile)

        if not self.nobuild:
            try:
                eupsServer.system(cmd, self.Eups.noaction)
            except OSError, e:
                if self.verbose >= 0 and os.path.exists(logfile):
                    try: 
                        print >> self.log, "BUILD ERROR!  From build log:"
                        eupsServer.system("tail -20 %s 1>&2" % logfile)
                    except:
                        pass
                raise RuntimeError("Failed to build %s: %s" % (builder, str(e)))

            if self.verbose > 0:
                print >> self.log, "Builder %s successfully completed" % builder

    def findTableFile(self, productName, version, flavor):
        """Give the distrib a chance to produce a table file"""

        return self.find_file_on_path("%s.table" % productName)

    def find_file_on_path(self, fileName, auxDir = None):
        """Look for a fileName on the :-separated buildFilePath, looking in auxDir if
        an element of path is empty"""

        if not self.buildFilePath:
            return None

        for bd in self.buildFilePath.split(":"):
            bd = os.path.expandvars(os.path.expanduser(bd))
            
            if bd == "":
                if auxDir:
                    bd = auxDir
                else:
                    continue

            if re.match(bd, r"\*%"):    # search recursively for desired file
                bd = bd[0:-1]
                if self.verbose > 2:
                    print "Searching %s recursively for %s)" % (bd, fileName)
                
                for dir, subDirs, files in os.walk(bd):
                    if dir == ".svn":   # don't look in SVN private directories
                        continue
                    
                    for f in files:
                        if f == fileName:
                            full_fileName = os.path.join(dir, fileName)

                            if self.verbose > 1:
                                print "Found %s (%s)" % (fileName, full_fileName)
                            return full_fileName

            full_fileName = os.path.join(bd, fileName)

            if os.path.exists(full_fileName):
                if self.verbose > 1:
                    print "Found %s (%s)" % (fileName, full_fileName)
                return full_fileName

        return None

#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-

class BuildfilePatchCallbacks(object):
    """Callbacks to modify build files.
    
     E.g. we can define a callback to rewrite SVN root to recognise a tagname
      "svnXXX" as a request for release XXX
      """

    callbacks = []
    user_callbacks = []

    def __init__(self):
        pass

    def add(self, callback, system=False):
        """Declare a callback to modify a build file; iff system is true, it's a "system" callback
        which isn't deleted (by default) by the clear member function

        The callbacks accept a line of the file, and return a possibly
        modified version of the same line

        N.b.  A version specification ABC will appear in an SVN root as tags/ABC;
        e.g. the default callback list includes
           lambda line: re.sub(r"/tags/svn", "/trunk -r ", line)
        """
        if system:
            BuildfilePatchCallbacks.callbacks += [callback]
        else:
            BuildfilePatchCallbacks.user_callbacks += [callback]

    def apply(self, line):
        """Apply all callbacks to a line (system callbacks are applied first"""
        
        for c in BuildfilePatchCallbacks.callbacks + BuildfilePatchCallbacks.user_callbacks:
            line = c(line)

        return line

    def clear(self, user=True, system=False):
        """Clear the list of buildfile patch callbacks
        
        If user is True, clear  user-defined callbacks
        If system is True, clear system-defined callbacks
        """
        if user:
            BuildfilePatchCallbacks.user_callbacks = []
        if system:
            BuildfilePatchCallbacks.callbacks = []

try:
    type(buildfilePatchCallbacks)
except NameError:
    buildfilePatchCallbacks = BuildfilePatchCallbacks()
    #
    # Recognise that a tagname svnXYZ is version XYZ on the trunk 
    #
    buildfilePatchCallbacks.add(lambda line: re.sub(r"/tags/svn", "/trunk -r ", line), system=True)

#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
#
# Expand a build file
#
def expandBuildFile(ofd, ifd, productName, versionName, verbose=False, builderVars={}, repoVersionName=None):
    """Expand a build file, reading from ifd and writing to ofd"""
    #
    # A couple of functions to set/guess the values that we'll be substituting
    # into the build file
    #
    # Guess the value of CVSROOT
    #
    def guess_cvsroot(cvsroot):
        if cvsroot:
            pass
        elif os.environ.has_key("CVSROOT"):
            cvsroot = os.environ["CVSROOT"]
        elif os.path.isdir("CVS"):
            try:
                rfd = open("CVS/Root")
                cvsroot = re.sub(r"\n$", "", rfd.readline())
                del rfd
            except IOError, e:
                print >> sys.stderr, "Tried to read \"CVS/Root\" but failed: %s" % e

        return cvsroot    
    #
    # Guess the value of SVNROOT
    #
    def guess_svnroot(svnroot):
        if svnroot:
            pass
        elif os.environ.has_key("SVNROOT"):
            svnroot = os.environ["SVNROOT"]
        elif os.path.isdir(".svn"):
            try:
                rfd = os.popen("svn info .")
                for line in rfd:
                    mat = re.search(r"^Repository Root: (\S+)", line)
                    if mat:
                        svnroot = mat.group(1)
                        break

                if not svnroot:         # Repository Root is absent in svn 1.1
                    rfd = os.popen("svn info .svn")
                    for line in rfd:
                        mat = re.search(r"^URL: ([^/]+//[^/]+)", line)
                        if mat:
                            svnroot = mat.group(1)
                            break

                del rfd
            except IOError, e:
                print >> sys.stderr, "Tried to read \".svn\" but failed: %s" % e

        return svnroot
    #
    # Here's the function to do the substitutions
    #
    builderVars["CVSROOT"] = guess_cvsroot(builderVars.get("CVSROOT"))
    builderVars["SVNROOT"] = guess_svnroot(builderVars.get("SVNROOT"))
    builderVars["PRODUCT"] = productName
    builderVars["VERSION"] = versionName
    if repoVersionName is None:
        repoVersionName = versionName
    builderVars["REPOVERSION"] = repoVersionName

    def subVar(name):
        var = name.group(1)
        # variable may be of form @NAME.op@ (e.g. @VERSION.replace(".", "_")@)
        # which means expand name = @NAME@ and evaluate name.op

        dollar, var, op = re.search(r"^(\$)?([^\.]+)(?:\.(.*))?", var).groups()

        var = var.upper()

        if builderVars.has_key(var):
            if builderVars[var]:
                value = builderVars[var]
            else:
                print >> sys.stderr, "I can't guess a %s for you -- please set hooks.config.distrib[\"builder\"][\"variables\"][\"%s\"] or $%s" % (var, var, var)
                value = var

            while op:                      # a python operation to be applied to value.
                # We could just eval the expression, but that allows a malicious user to execute random python,
                # so we'll interpret the commands ourselves

                # regexp for replace commands
                replace_re = r"^replace\s*\(\s*r?[\"']([^\"']+)[\"']\s*,\s*r?[\"']([^\"']+)[\"']\s*\)"
                # regexp for regexp-based replace commands (@NAME.sub(s1, s2)@ --> re.sub(s1, s2, name))
                sub_re = r"^sub\s*\(\s*r?[\"']([^\"']+)[\"']\s*,\s*r?[\"']([^\"']*)[\"']\s*\)"

                if re.search(replace_re, op):
                    mat = re.search(replace_re, op)
                    op = op[len(mat.group(0)):]
                    value = value.replace(mat.group(1), mat.group(2))
                elif re.search(sub_re, op):
                    mat = re.search(sub_re, op)
                    op = op[len(mat.group(0)):]
                    value = re.sub(mat.group(1), mat.group(2), value)
                elif op == "lower()":
                    op = op[len(op):]
                    value = value.lower()
                elif op == "title()":
                    op = op[len(op):]
                    value = value.title()
                elif op == "upper()":
                    op = op[len(op):]
                    value = value.upper()
                else:
                    print >> sys.stderr, "Unexpected modifier \"%s\"; ignoring" % op

                if op and op[0] == ".": 
                    op = op[1:] 

            if dollar:                  # @$var@ expands to a sh variable with a default value
                value = "${%s:-%s}" % (var, value)
                
            return value

        return "XXX"
    #
    # Actually do the work
    #
    versionNameRe = re.escape(versionName)

    for line in ifd:
        line = line.rstrip()

        # HACK: if checking out from SVN with the current version name (@VERSION@), replace it by
        # the version name from the repository (@REPOVERSION@).
        # The right thing to do is to update the build files
        if re.search(r"^\s*(svn|cvs)\s+(co|checkout)", line):
            line = re.sub(r"(svn\S*:|http\S*:|@SVNROOT@|@CVSROOT@)(\S+/)@VERSION@(\S*)",
                          r"\1\2@REPOVERSION@\3", line)
        line = re.sub(r"(hg\s+up\s+)@VERSION@", r"\1@REPOVERSION@", line)

        # HACK:  replace "scons .* install" with "scons .* install version=@VERSION@ baseversion=@REPOVERSION@"
        # The right thing to do is to update the build files
        if re.search(r"^\s*scons\s+.*install", line):
            line = re.sub("\sversion=\S+", "", line)
            line += " version=@VERSION@"
            if not re.search(r"\sbaseversion=\S+", line):
                # Do not ever substitute!  This should remain untouched until the end of time.
                line += " baseversion=@REPOVERSION@"

        # NOTE: if you want to use build files created before the above HACKs were effective, you'll need to
        # hack the build files manually.  Try something like the following:
        #
        # sed -ri -e 's|(svn[^[:space:]]*:[^[:space:]]*)[abcdefg]_hsc|\1|' -e '/baseversion/! s|version=([HSCDC0-9.-]+)([abcdefg]_hsc)?|version=\1\2 baseversion=\1|' /path/to/builds/*.build
        #
        # The trick is to get the version regex correct; the above covers dotted number versions and
        # 'HSC-DC2'.  If you've got lower-case letters as the last characters in your version names, you may
        # have to edit by hand....

        # Attempt substitutions
        line = re.sub(r"@([^@]+)@", subVar, line)

        try:
            line = buildfilePatchCallbacks.apply(line)
        except RuntimeError, e:
            print >> sys.stderr, ("Warning: %s" % e)

        print >> ofd, line
