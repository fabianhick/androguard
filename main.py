# core modules
import os
import re
import shutil
import sys

# 3rd party modules
from lxml import etree
from loguru import logger
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters.terminal import TerminalFormatter

# internal modules
from androguard.core import androconf
from androguard.core import apk
from androguard.core.axml import AXMLPrinter
from androguard.util import readFile
from androguard.ui import DynamicUI

def androaxml_main(inp, outp=None, resource=None):
    ret_type = androconf.is_android(inp)
    if ret_type == "APK":
        a = apk.APK(inp)
        if resource:
            if resource not in a.files:
                logger.error("The APK does not contain a file called '{}'".format(resource), file=sys.stderr)
                sys.exit(1)

            axml = AXMLPrinter(a.get_file(resource)).get_xml_obj()
        else:
            axml = a.get_android_manifest_xml()
    elif ".xml" in inp:
        axml = AXMLPrinter(readFile(inp)).get_xml_obj()
    else:
        logger.error("Unknown file type")
        sys.exit(1)

    buff = etree.tostring(axml, pretty_print=True, encoding="utf-8")
    if outp:
        with open(outp, "wb") as fd:
            fd.write(buff)
    else:
        sys.stdout.write(highlight(buff.decode("UTF-8"), get_lexer_by_name("xml"), TerminalFormatter()))


def androarsc_main(arscobj, outp=None, package=None, typ=None, locale=None):
    package = package or arscobj.get_packages_names()[0]
    ttype = typ or "public"
    locale = locale or '\x00\x00'

    # TODO: be able to dump all locales of a specific type
    # TODO: be able to recreate the structure of files when developing, eg a
    # res folder with all the XML files

    if not hasattr(arscobj, "get_{}_resources".format(ttype)):
        print("No decoder found for type: '{}'! Please open a bug report."
              .format(ttype),
              file=sys.stderr)
        sys.exit(1)

    x = getattr(arscobj, "get_" + ttype + "_resources")(package, locale)

    buff = etree.tostring(etree.fromstring(x),
                          pretty_print=True,
                          encoding="UTF-8")

    if outp:
        with open(outp, "wb") as fd:
            fd.write(buff)
    else:
        sys.stdout.write(highlight(buff.decode("UTF-8"), get_lexer_by_name("xml"), TerminalFormatter()))

def androcg_main(verbose,
                 APK,
                 classname,
                 methodname,
                 descriptor,
                 accessflag,
                 no_isolated,
                 show,
                 output):
    from androguard.core.androconf import show_logging
    from androguard.core.bytecode import FormatClassToJava
    from androguard.misc import AnalyzeAPK
    import networkx as nx
    import logging
    log = logging.getLogger("androcfg")
    if verbose:
        show_logging(logging.INFO)

    a, d, dx = AnalyzeAPK(APK)

    entry_points = map(FormatClassToJava,
                       a.get_activities() + a.get_providers() +
                       a.get_services() + a.get_receivers())
    entry_points = list(entry_points)

    log.info("Found The following entry points by search AndroidManifest.xml: "
             "{}".format(entry_points))

    CG = dx.get_call_graph(classname,
                           methodname,
                           descriptor,
                           accessflag,
                           no_isolated,
                           entry_points,
                           )

    write_methods = dict(gml=_write_gml,
                         gexf=nx.write_gexf,
                         gpickle=nx.write_gpickle,
                         graphml=nx.write_graphml,
                         yaml=nx.write_yaml,
                         net=nx.write_pajek,
                         )

    if show:
        plot(CG)
    else:
        writer = output.rsplit(".", 1)[1]
        if writer in ["bz2", "gz"]:
            writer = output.rsplit(".", 2)[1]
        if writer not in write_methods:
            print("Could not find a method to export files to {}!"
                  .format(writer))
            sys.exit(1)

        write_methods[writer](CG, output)


def plot(cg):
    """
    Plot the call graph using matplotlib
    For larger graphs, this should not be used, as it is very slow
    and probably you can not see anything on it.

    :param cg: A networkx call graph to plot
    """
    import matplotlib.pyplot as plt
    import networkx as nx
    pos = nx.spring_layout(cg)

    internal = []
    external = []

    for n in cg.nodes:
        if n.is_external():
            external.append(n)
        else:
            internal.append(n)

    nx.draw_networkx_nodes(cg, pos=pos, node_color='r', nodelist=internal)
    nx.draw_networkx_nodes(cg, pos=pos, node_color='b', nodelist=external)
    nx.draw_networkx_edges(cg, pos, arrows=True)
    nx.draw_networkx_labels(cg, pos=pos, labels={x: "{}{}".format(x.class_name, x.name) for x in cg.nodes})
    plt.draw()
    plt.show()


def _write_gml(G, path):
    """
    Wrapper around nx.write_gml
    """
    import networkx as nx
    return nx.write_gml(G, path, stringizer=str)

def export_apps_to_format(filename,
                          s,
                          output,
                          methods_filter=None,
                          jar=None,
                          decompiler_type=None,
                          form=None):
    from androguard.misc import clean_file_name
    from androguard.core.dex import DEX
    from androguard.core.bytecode import method2dot, method2format, method2simpledot, method2dotaggregated
    from androguard.decompiler import decompiler
    from androguard.core.bytecode import FormatClassToJava
    import networkx as nx

    logger.info("Dump information {} in {}".format(filename, output))

    if not os.path.exists(output):
        logger.info("Create directory %s" % output)
        os.makedirs(output)
    else:
        logger.info("Clean directory %s" % output)
        androconf.rrmdir(output)
        os.makedirs(output)

    methods_filter_expr = None
    if methods_filter:
        methods_filter_expr = re.compile(methods_filter)
    
    a, _, dx = s.get_objects_apk(filename)

    cg_output = os.path.join(output, "callgraph.gml")
    classname = '.*'
    methodname = '.*'
    descriptor = '.*'
    accessflag = '.*'
    no_isolated = False

    entry_points = map(FormatClassToJava,
                    a.get_activities() + a.get_providers() +
                    a.get_services() + a.get_receivers())
    entry_points = list(entry_points)

    logger.info("Found The following entry points by search AndroidManifest.xml: "  "{}".format(entry_points))

    CG, cg_nodes_by_label, cg_nodes_by_external  = dx.get_modified_call_graph(classname,
                            methodname,
                            descriptor,
                            accessflag,
                            no_isolated,
                            entry_points,
                            )

    write_methods = dict(gml=_write_gml,
                            gexf=nx.write_gexf,
                            #gpickle=nx.write_gpickle,
                            graphml=nx.write_graphml,
                            #yaml=nx.write_yaml,
                            net=nx.write_pajek,
                            )
    
    writer = cg_output.rsplit(".", 1)[1]
    if writer in ["bz2", "gz"]:
        writer = cg_output.rsplit(".", 2)[1]
    if writer not in write_methods:
        logger.error("Could not find a method to export files to {}!".format(writer))
        sys.exit(1)
    write_methods[writer](CG, cg_output)
    #logger.info(CG.nodes)

    dump_classes = []
    cfg_output = os.path.join(output, "data.json")
    import networkx as nx
    from collections import OrderedDict
    import pandas as pd
    import json

    with open(cfg_output, 'a') as file:
        methods = OrderedDict()
        method_names = []
        file.write('{"acfg_list": [')
        for _, vm, vmx in s.get_objects_dex():
            logger.info("Decompilation ...", end=' ')
            sys.stdout.flush()

            if decompiler_type == "dex2jad":
                vm.set_decompiler(decompiler.DecompilerDex2Jad(vm,
                                                            androconf.CONF["BIN_DEX2JAR"],
                                                            androconf.CONF["BIN_JAD"],
                                                            androconf.CONF["TMP_DIRECTORY"]))
            elif decompiler_type == "dex2winejad":
                vm.set_decompiler(decompiler.DecompilerDex2WineJad(vm,
                                                                androconf.CONF["BIN_DEX2JAR"],
                                                                androconf.CONF["BIN_WINEJAD"],
                                                                androconf.CONF["TMP_DIRECTORY"]))
            elif decompiler_type == "ded":
                vm.set_decompiler(decompiler.DecompilerDed(vm,
                                                        androconf.CONF["BIN_DED"],
                                                        androconf.CONF["TMP_DIRECTORY"]))
            elif decompiler_type == "dex2fernflower":
                vm.set_decompiler(decompiler.DecompilerDex2Fernflower(vm,
                                                                    androconf.CONF["BIN_DEX2JAR"],
                                                                    androconf.CONF["BIN_FERNFLOWER"],
                                                                    androconf.CONF["OPTIONS_FERNFLOWER"],
                                                                    androconf.CONF["TMP_DIRECTORY"]))

            

            if jar:
                print("jar ...", end=' ')
                filenamejar = decompiler.Dex2Jar(vm,
                                                androconf.CONF["BIN_DEX2JAR"],
                                                androconf.CONF["TMP_DIRECTORY"]).get_jar()
                shutil.move(filenamejar, os.path.join(output, "classes.jar"))
                print("End")
            logger.info("Dumping cfgs...")
            
            first_iteration = True

            for method in vm.get_methods():
                if methods_filter_expr:
                    msig = "{}{}{}".format(method.get_class_name(), method.get_name(),
                                    method.get_descriptor())
                    if not methods_filter_expr.search(msig):
                        continue

                # Current Folder to write to
                #filename_class = valid_class_name(str(method.get_class_name()))
                #filename_class = os.path.join(output, filename_class)
                #create_directory(filename_class)

                #print("Dump {} {} {} ...".format(method.get_class_name(),
                #                             method.get_name(),
                #                             method.get_descriptor()), end=' ')

                #filename = clean_file_name(os.path.join(filename_class, method.get_short_string()))

                #buff = method2dot(vmx.get_method(method))
                if len(list(vmx.get_method(method).get_method().get_instructions())) == 0:
                    continue
                method_name = "{}{}{}".format(method.get_class_name(), method.get_name(),method.get_descriptor())
                method_id = len(methods)
                methods[method_name] = method_id
                method_names.append(method_name)

                cfg = method2dotaggregated(vmx.get_method(method))
                
                cfgdf = nx.to_pandas_edgelist(cfg)

                cfg_edges_s = list(cfgdf.source)
                cfg_edges_t = list(cfgdf.target)
                cfg_features = nx.get_node_attributes(cfg, "features")
                assert len(cfg_features) == cfg.number_of_nodes()
                data = {"edges": [cfg_edges_s, cfg_edges_t], "features": cfg_features}
                if(not first_iteration):
                    file.write(",\n")
                else:
                    first_iteration = False
                file.write(json.dumps(data))
                # Write Graph of method
                #if form:
                #    print("%s ..." % form, end=' ')
                #    method2format(filename + "." + form, form, None, buff)

                # Write the Java file for the whole class
                #if str(method.get_class_name()) not in dump_classes:
                #    print("source codes ...", end=' ')
                #    current_class = vm.get_class(method.get_class_name())
                #    current_filename_class = valid_class_name(str(current_class.get_name()))

                #    current_filename_class = os.path.join(output, current_filename_class + ".java")
                #    with open(current_filename_class, "w") as fd:
                #        fd.write(current_class.get_source())
                #    dump_classes.append(method.get_class_name())

                # Write SMALI like code
                #print("bytecodes ...", end=' ')
                #bytecode_buff = DEX.get_bytecodes_method(vm, vmx, method)
                #with open(filename + ".ag", "w") as fd:
                #    fd.write(bytecode_buff)
                #print()
            logger.info("End dumping cfgs!")
            
            rename_nodes = {}
            # Filter the call graph attributes by external or not and rename the nodes in the new graph
            num_local_functions = len(method_names)
            counter = num_local_functions
            CG = nx.convert_node_labels_to_integers(CG)
            for i in range(len(cg_nodes_by_label)):
                if cg_nodes_by_external[i]:
                    rename_nodes[i] = counter
                    counter = counter + 1
                    method_names.append(cg_nodes_by_label[i])
                    #assert len(method_names) == counter
                else:
                    rename_nodes[i] = method_names.index(cg_nodes_by_label[i])

            CG = nx.relabel_nodes(CG, rename_nodes)

            cgdf = nx.to_pandas_edgelist(CG)
            cg_edges_s = list(cgdf.source)
            cg_edges_t = list(cgdf.target)

            file.write('],\n "cg_edges": [' + str(cg_edges_s) + "," + str(cg_edges_t) + "],\n")
            file.write('"method_names": ' + json.dumps(method_names) + "}")


            logger.info("End Decompilation")

def valid_class_name(class_name):
    if class_name[-1] == ";":
        class_name = class_name[1:-1]
    return os.path.join(*class_name.split("/"))


def create_directory(pathdir):
    if not os.path.exists(pathdir):
        os.makedirs(pathdir)


def androlyze_main(session, filename):
    """
    Start an interactive shell

    :param session: Session file to load
    :param filename: File to analyze, can be APK or DEX (or ODEX)
    """
    from colorama import Fore
    import colorama
    import atexit
    
    from IPython.terminal.embed import embed

    from traitlets.config import Config
    
    from androguard.core.androconf import ANDROGUARD_VERSION, CONF
    from androguard.session import Session
    from androguard.core import dex, apk
    from androguard.core.analysis.analysis import Analysis
    from androguard.pentest import Pentest
    from androguard.ui import DynamicUI
    from androguard.misc import AnalyzeAPK

    colorama.init()

    if session:
        logger.info("Restoring session '{}'...".format(session))
        s = CONF['SESSION'] = Load(session)
        logger.info("Successfully restored {}".format(s))
        # TODO Restore a, d, dx etc...
    else:
        s = CONF["SESSION"] = Session(export_ipython=True)

    if filename:
        ("Loading apk {}...".format(os.path.basename(filename)))
        logger.info("Please be patient, this might take a while.")

        filetype = androconf.is_android(filename)

        logger.info("Found the provided file is of type '{}'".format(filetype))

        if filetype not in ['DEX', 'DEY', 'APK']:
            logger.error(Fore.RED + "This file type is not supported by androlyze for auto loading right now!" + Fore.RESET, file=sys.stderr)
            logger.error("But your file is still available:")
            logger.error(">>> filename")
            logger.error(repr(filename))
            print()

        else:
            with open(filename, "rb") as fp:
                raw = fp.read()

            h = s.add(filename, raw)
            logger.info("Added file to session: SHA256::{}".format(h))

            if filetype == 'APK':
                logger.info("Loaded APK file...")
                a, d, dx = s.get_objects_apk(digest=h)

                print(">>> filename")
                print(filename)
                print(">>> a")
                print(a)
                print(">>> d")
                print(d)
                print(">>> dx")
                print(dx)
                print()
            elif filetype in ['DEX', 'DEY']:
                logger.info("Loaded DEX file...")
                for h_, d, dx in s.get_objects_dex():
                    if h == h_:
                        break
                print(">>> d")
                print(d)
                print(">>> dx")
                print(dx)
                print()

    def shutdown_hook():
        """Save the session on exit, if wanted"""
        if not s.isOpen():
            return

        try:
            res = input("Do you want to save the session? (y/[n])?").lower()
        except (EOFError, KeyboardInterrupt):
            pass
        else:
            if res == "y":
                # TODO: if we already started from a session, probably we want to save it under the same name...
                # TODO: be able to take any filename you want
                fname = s.save()
                print("Saved Session to file: '{}'".format(fname))

    cfg = Config()
    _version_string = "Androguard version {}".format(ANDROGUARD_VERSION)
    ipshell = embed(config=cfg, banner1="{} started".format(_version_string))
    atexit.register(shutdown_hook)
    ipshell()


def androsign_main(args_apk, args_hash, args_all, show):
    from androguard.core.apk import APK
    from androguard.util import get_certificate_name_string

    import hashlib
    import binascii
    import traceback
    from colorama import Fore, Style
    from asn1crypto import x509, keys

    # Keep the list of hash functions in sync with cli/entry_points.py:sign
    hashfunctions = dict(md5=hashlib.md5,
                         sha1=hashlib.sha1,
                         sha256=hashlib.sha256,
                         sha512=hashlib.sha512,
                         )

    if args_hash.lower() not in hashfunctions:
        print("Hash function {} not supported!"
              .format(args_hash.lower()), file=sys.stderr)
        print("Use one of {}"
              .format(", ".join(hashfunctions.keys())), file=sys.stderr)
        sys.exit(1)

    for path in args_apk:
        try:
            a = APK(path)

            print("{}, package: '{}'".format(os.path.basename(path), a.get_package()))
            print("Is signed v1: {}".format(a.is_signed_v1()))
            print("Is signed v2: {}".format(a.is_signed_v2()))
            print("Is signed v3: {}".format(a.is_signed_v3()))

            certs = set(a.get_certificates_der_v3() + a.get_certificates_der_v2() + [a.get_certificate_der(x) for x in a.get_signature_names()])
            pkeys = set(a.get_public_keys_der_v3() + a.get_public_keys_der_v2())

            if len(certs) > 0:
                print("Found {} unique certificates".format(len(certs)))

            for cert in certs:
                if show:
                    x509_cert = x509.Certificate.load(cert)
                    print("Issuer:", get_certificate_name_string(x509_cert.issuer, short=True))
                    print("Subject:", get_certificate_name_string(x509_cert.subject, short=True))
                    print("Serial Number:", hex(x509_cert.serial_number))
                    print("Hash Algorithm:", x509_cert.hash_algo)
                    print("Signature Algorithm:", x509_cert.signature_algo)
                    print("Valid not before:", x509_cert['tbs_certificate']['validity']['not_before'].native)
                    print("Valid not after:", x509_cert['tbs_certificate']['validity']['not_after'].native)

                if not args_all:
                    print("{} {}".format(args_hash.lower(), hashfunctions[args_hash.lower()](cert).hexdigest()))
                else:
                    for k, v in hashfunctions.items():
                        print("{} {}".format(k, v(cert).hexdigest()))
                print()

            if len(certs) > 0:
                print("Found {} unique public keys associated with the certs".format(len(pkeys)))

            for public_key in pkeys:
                if show:
                    x509_public_key = keys.PublicKeyInfo.load(public_key)
                    print("PublicKey Algorithm:", x509_public_key.algorithm)
                    print("Bit Size:", x509_public_key.bit_size)
                    print("Fingerprint:", binascii.hexlify(x509_public_key.fingerprint))
                    try:
                        print("Hash Algorithm:", x509_public_key.hash_algo)
                    except ValueError as ve:
                        # RSA pkey does not have an hash algorithm
                        pass
                print()


        except:
            print(Fore.RED + "Error in {}".format(os.path.basename(path)) + Style.RESET_ALL, file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

        if len(args_apk) > 1:
            print()


def androdis_main(offset, size, dex_file):
    from androguard.core.dex import DEX

    with open(dex_file, "rb") as fp:
        buf = fp.read()
    
    d = DEX(buf)

    if size == 0 and offset == 0:
        # Assume you want to just get a disassembly of all classes and methods
        for cls in d.get_classes():
            print("# CLASS: {}".format(cls.get_name()))
            for m in cls.get_methods():
                print("## METHOD: {} {} {}".format(m.get_access_flags_string(), m.get_name(), m.get_descriptor()))
                for idx, ins in m.get_instructions_idx():
                    print('{:08x}  {}'.format(idx, ins.disasm()))

                print()
            print()
    else:
        if size == 0:
            size = len(buf)

        if d:
            idx = offset
            for nb, i in enumerate(d.disassemble(offset, size)):
                print("%-8d(%08x)" % (nb, idx), end=' ')
                i.show(idx)
                print()

                idx += i.get_length()

def androtrace_main(apk_file, list_modules, live=False, enable_ui=False):
    from androguard.pentest import Pentest
    from androguard.session import Session

    s = Session()

    if not live:
        with open(apk_file, "rb") as fp:
            raw = fp.read()

        h = s.add(apk_file, raw)
        logger.info("Added file to session: SHA256::{}".format(h))

    p = Pentest()
    p.print_devices()
    p.connect_default_usb()
    p.start_trace(apk_file, s, list_modules, live=live)

    if enable_ui:
        logger.remove(1)
        from prompt_toolkit.eventloop.inputhook import InputHookContext, set_eventloop_with_inputhook
        from prompt_toolkit.application import get_app
        import time

        time.sleep(1)
        
        ui = DynamicUI(p.message_queue)
        def inputhook(inputhook_context: InputHookContext):
            while not inputhook_context.input_is_ready():
                if ui.process_data():
                    get_app().invalidate()
                else:
                    time.sleep(0.1)

        set_eventloop_with_inputhook(inputhook=inputhook)

        ui.run()
    else:
        logger.warning("Type 'e' to exit the strace ")
        s = ""
        while (s!='e') and (not p.is_detached()):
            s = input("Type 'e' to exit:")    


def androdump_main(package_name, list_modules):
    from androguard.pentest import Pentest
    from androguard.session import Session

    s = Session()

    p = Pentest()
    p.print_devices()
    p.connect_default_usb()
    p.start_trace(package_name, s, list_modules, live=True, dump=True)
