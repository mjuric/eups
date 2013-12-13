SHELL = /bin/sh

SUBDIRS=eupspkg

all :;

install :
	@ for f in $(SUBDIRS); do \
		(\
		   if [ ! -d $(EUPS_DIR)/$$f ]; then mkdir $(EUPS_DIR)/lib/$$f; fi; \
		   cd $$f ; \
		   echo In $$f; $(MAKE) $(MFLAGS) install \
                ); \
	done

clean :
	- /bin/rm -f *~ core
