from rest_framework import viewsets
from crashmanager.serializers import BucketSerializer, CrashEntrySerializer
from crashmanager.models import CrashEntry, Bucket, BugProvider, Bug, Tool, User
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from FTB.Signatures.CrashInfo import CrashInfo
from FTB.ProgramConfiguration import ProgramConfiguration
from django.core.exceptions import SuspiciousOperation
from django.db.models import Q
from django.db.models.aggregates import Count, Min
from django.http.response import Http404
from rest_framework.authentication import TokenAuthentication
from datetime import datetime, timedelta
import operator

def renderError(request, err):
    return render(request, 'error.html', { 'error_message' : err })

def logout_view(request):
    logout(request)
    return redirect('crashmanager:index')

@login_required(login_url='/login/')
def index(request):
    return redirect('crashmanager:crashes')

@login_required(login_url='/login/')
def stats(request):
    lastHourDelta = datetime.now() - timedelta(hours=1)
    print(lastHourDelta)
    entries = CrashEntry.objects.filter(created__gt = lastHourDelta)
    
    bucketFrequencyMap = {}
    for entry in entries:
        if entry.bucket != None:
            if entry.bucket.pk in bucketFrequencyMap:
                bucketFrequencyMap[entry.bucket.pk] += 1
            else:
                bucketFrequencyMap[entry.bucket.pk] = 1
    
    frequentBuckets = []
    
    if bucketFrequencyMap:
        bucketFrequencyMap = sorted(bucketFrequencyMap.items(), key=lambda t: t[1], reverse=True)[:10]
        for pk, freq in bucketFrequencyMap:
            obj = Bucket.objects.get(pk=pk)
            obj.rph = freq
            frequentBuckets.append(obj)
        
    return render(request, 'stats.html', { 'total_reports_per_hour': len(entries), 'frequentBuckets' : frequentBuckets })

@login_required(login_url='/login/')
def settings(request):
    return render(request, 'settings.html')

@login_required(login_url='/login/')
def allSignatures(request):
    entries = Bucket.objects.annotate(size=Count('crashentry'), quality=Min('crashentry__testcase__quality'))
    return render(request, 'signatures/index.html', { 'isAll': True, 'siglist' : entries })

@login_required(login_url='/login/')
def allCrashes(request):
    entries = CrashEntry.objects.all().order_by('-id')
    return render(request, 'crashes/index.html', { 'isAll': True, 'crashlist' : entries })

@login_required(login_url='/login/')
def signatures(request):
    entries = Bucket.objects.all().annotate(size=Count('crashentry'),quality=Min('crashentry__testcase__quality'))

    filters = {}
    q = None
    isSearch = False
    
    # These are all keys that are allowed for exact filtering
    exactFilterKeys = [
                       "bug",
                       "shortDescription__contains",
                       "signature__contains",
                       ]
    
    for key in exactFilterKeys:
        if key in request.GET:
            isSearch = True
            filters[key] = request.GET[key]
        
    if "q" in request.GET:
        isSearch = True
        q = request.GET["q"]
        entries = entries.filter(
                                 Q(shortDescription__contains=q)
                                 | Q(signature__contains=q)
                                 )
    
    if "ids" in request.GET:
        isSearch = True
        ids = [int(x) for x in request.GET["ids"].split(",")]
        entries = entries.filter(pk__in=ids)
        
    # Do not display triaged crash entries unless there is an all=1 parameter
    # specified in the search query. Otherwise only show untriaged entries.
    if not "all" in request.GET or not request.GET["all"]:
        filters["bug"] = None
    
    entries = entries.filter(**filters)
    
    data = { 'q' : q, 'request' : request, 'isSearch' : isSearch, 'siglist' : entries }
    return render(request, 'signatures/index.html', data)

@login_required(login_url='/login/')
def crashes(request):
    filters = {}
    q = None
    isSearch = True
    
    entries = CrashEntry.objects.all().order_by('-id')
    
    (user, created) = User.objects.get_or_create(user = request.user)
    defaultToolsFilter = user.defaultToolsFilter.all()
    if defaultToolsFilter:
        entries = entries.filter(reduce(operator.or_, [Q(("tool",x)) for x in defaultToolsFilter]))
    
    # These are all keys that are allowed for exact filtering
    exactFilterKeys = [
                       "bucket",
                       "client__name",
                       "client__name__contains",
                       "os__name",
                       "product__name",
                       "product__version",
                       "platform__name",
                       "testcase__quality",
                       "tool__name",
                       ]
    
    for key in exactFilterKeys:
        if key in request.GET:
            filters[key] = request.GET[key]
    
    if "sig" in request.GET:
        filters["shortSignature__contains"] = request.GET["sig"]
        
    if "q" in request.GET:
        q = request.GET["q"]
        entries = entries.filter(
                                 Q(shortSignature__contains=q)
                                 | Q(rawStderr__contains=q)
                                 | Q(rawCrashData__contains=q)
                                 | Q(args__contains=q)
                                 )
    
    # If we don't have any filters up to this point, don't consider it a search
    if not filters and q == None:        
        isSearch = False
        
    # Do not display triaged crash entries unless there is an all=1 parameter
    # specified in the search query. Otherwise only show untriaged entries.
    if not "all" in request.GET or not request.GET["all"]:
        filters["bucket"] = None
    
    entries = entries.filter(**filters)
    data = { 'q' : q, 'request' : request, 'isSearch' : isSearch, 'crashlist' : entries }
    
    return render(request, 'crashes/index.html', data)

@login_required(login_url='/login/')
def autoAssignCrashEntries(request):
    entries = CrashEntry.objects.filter(bucket=None)
    buckets = Bucket.objects.all()
    
    for bucket in buckets:
        signature = bucket.getSignature()
        needTest = signature.matchRequiresTest()
        
        for entry in entries:
            if signature.matches(entry.getCrashInfo(attachTestcase=needTest)):
                entry.bucket = bucket
                entry.save()
    
    return redirect('crashmanager:crashes')

@login_required(login_url='/login/')
def viewCrashEntry(request, crashid):
    entry = get_object_or_404(CrashEntry, pk=crashid)
    entry.deserializeFields()
    
    if entry.testcase and not entry.testcase.isBinary:
        entry.testcase.loadTest()
    
    return render(request, 'crashes/view.html', { 'entry' : entry })

@login_required(login_url='/login/')
def editCrashEntry(request, crashid):
    entry = get_object_or_404(CrashEntry, pk=crashid)
    entry.deserializeFields()
    
    if entry.testcase:
        entry.testcase.loadTest()
    
    if request.method == 'POST':
        entry.rawStdout = request.POST['rawStdout']
        entry.rawStderr = request.POST['rawStderr']
        entry.rawStderr = request.POST['rawStderr']
        entry.rawCrashData = request.POST['rawCrashData']
        
        entry.envList = request.POST['env'].splitlines()
        entry.argsList = request.POST['args'].splitlines()
        entry.metadataList = request.POST['metadata'].splitlines()
        
        # Regenerate crash information and fields depending on it
        crashInfo = entry.getCrashInfo()
        if crashInfo.crashAddress != None:
            entry.crashAddress = hex(crashInfo.crashAddress)
        entry.shortSignature = crashInfo.createShortSignature()
        
        if entry.testcase:
            if entry.testcase.isBinary:
                if request.POST['testcase'] != "(binary)":
                    entry.testcase.content = request.POST['testcase']
                    entry.testcase.isBinary = False
                    #TODO: The file extension stored on the server remains and is likely to be wrong
                    entry.testcase.storeTestAndSave()
            else:
                if request.POST['testcase'] != entry.testcase.content:
                    entry.testcase.content = request.POST['testcase']
                    entry.testcase.storeTestAndSave()
                    
                # Directly attach the testcase here, since we have it
                crashInfo.testcase = entry.testcase.content
        
        # If the entry has a bucket, check if it still fits into
        # this bucket, otherwise remove it.
        if entry.bucket:
            sig = entry.bucket.getSignature()
            if not sig.matches(crashInfo):
                entry.bucket = None
        
        entry.save()
        return redirect('crashmanager:crashview', crashid = entry.pk)
    else:
        return render(request, 'crashes/edit.html', { 'entry' : entry })
    
@login_required(login_url='/login/')
def deleteCrashEntry(request, crashid):
    entry = get_object_or_404(CrashEntry, pk=crashid)
    if request.method == 'POST':            
        entry.delete()
        return redirect('crashmanager:crashes')
    elif request.method == 'GET':
        return render(request, 'crashes/remove.html', { 'entry' : entry })
    else:
        raise SuspiciousOperation

def __handleSignaturePost(request, bucket):
    # This method contains code shared between newSignature and editSignature
    # and handles the POST request processing after the bucket object has been
    # either fetched or created.
    try:
        signature = bucket.getSignature()
    except RuntimeError, e:
        data = { 'bucket' : bucket, 'error_message' : 'Signature is not valid: %s' % e }
        return render(request, 'signatures/edit.html', data)
    
    # Only save if we hit "save" (not e.g. "preview")
    if 'submit_save' in request.POST:
        bucket.save()
    
    # If the reassign checkbox is checked, assign all unassigned issues that match
    # our signature to this bucket. Furthermore, remove all non-matching issues
    # from our bucket.
    #
    # Again, we only actually save if we hit "save". For previewing, we just count
    # how many issues would be assigned and removed.
    if "reassign" in request.POST:
        (inCount, outCount) = (0,0)
        
        signature = bucket.getSignature()
        needTest = signature.matchRequiresTest()
        entries = CrashEntry.objects.filter(Q(bucket=None) | Q(bucket=bucket))
        
        for entry in entries:
            match = signature.matches(entry.getCrashInfo(attachTestcase=needTest))
            if match and entry.bucket == None:
                inCount += 1
                if 'submit_save' in request.POST:
                    entry.bucket = bucket
                    entry.save()
            elif not match and entry.bucket != None:
                outCount += 1
                if 'submit_save' in request.POST:
                    entry.bucket = None
                    entry.save()
    
    # Save bucket and redirect to viewing it       
    if 'submit_save' in request.POST:
        return redirect('crashmanager:sigview', sigid=bucket.pk)
    
    # Render the preview page
    data = { 
            'bucket' : bucket, 
            'error_message' : "This is a preview, don't forget to save!",
            'inCount' : inCount, 'outCount' : outCount
            }
    return render(request, 'signatures/edit.html', data)

@login_required(login_url='/login/')
def newSignature(request):
    if request.method == 'POST':
        #TODO: FIXME: Update bug here as well
        bucket = Bucket(
                        signature=request.POST['signature'], 
                        shortDescription=request.POST['shortDescription'],
                        frequent="frequent" in request.POST
                        )
        return __handleSignaturePost(request, bucket)
    elif request.method == 'GET':
        if 'crashid' in request.GET:
            crashEntry = get_object_or_404(CrashEntry, pk=request.GET['crashid'])
            
            configuration = ProgramConfiguration(crashEntry.product.name, 
                                                 crashEntry.platform.name, 
                                                 crashEntry.os.name, 
                                                 crashEntry.product.version)
            
            crashInfo = CrashInfo.fromRawCrashData(crashEntry.rawStdout, 
                                                   crashEntry.rawStderr, 
                                                   configuration, 
                                                   crashEntry.rawCrashData)
            
            
            maxStackFrames = 8
            forceCrashInstruction = False
            forceCrashAddress = True
            errorMsg = None
            
            if 'stackframes' in request.GET:
                maxStackFrames = int(request.GET['stackframes'])
                
            if 'forcecrashaddress' in request.GET:
                forceCrashAddress = bool(int(request.GET['forcecrashaddress']))
            
            if 'forcecrashinstruction' in request.GET:
                forceCrashInstruction = bool(int(request.GET['forcecrashinstruction']))
            
            # First try to create the signature with the crash address included.
            # However, if that fails, try without forcing the crash signature.
            proposedSignature = crashInfo.createCrashSignature(
                                                               forceCrashAddress=forceCrashAddress,
                                                               forceCrashInstruction=forceCrashInstruction,
                                                               maxFrames = maxStackFrames
                                                               )
            if (proposedSignature == None):
                errorMsg = crashInfo.failureReason
                proposedSignature = crashInfo.createCrashSignature(maxFrames = maxStackFrames)
                
            proposedSignature = str(proposedSignature)
            proposedShortDesc = crashInfo.createShortSignature()
            
            data = { 'new' : True, 'bucket' : { 
                                                'pk' : None, 
                                                'bug' : None,
                                                'signature' : proposedSignature,
                                                'shortDescription' : proposedShortDesc
                                            },
                     'error_message' : errorMsg
                   }
        else:
            data = { 'new' : True }
    else:
        raise SuspiciousOperation
        
    return render(request, 'signatures/edit.html', data)

@login_required(login_url='/login/')
def deleteSignature(request, sigid):
    bucket = get_object_or_404(Bucket, pk=sigid)
    if request.method == 'POST':    
        # Make sure we remove this bucket from all crash entries referring to it,
        # otherwise these would be deleted as well through cascading.
        CrashEntry.objects.filter(bucket=bucket).update(bucket=None)
        
        bucket.delete()
        return redirect('crashmanager:signatures')
    elif request.method == 'GET':
        return render(request, 'signatures/remove.html', { 'bucket' : bucket })
    else:
        raise SuspiciousOperation

@login_required(login_url='/login/')
def viewSignature(request, sigid):
    #bucket = get_object_or_404(Bucket, pk=sigid)
    #count = len(CrashEntry.objects.filter(bucket=bucket))
    bucket = Bucket.objects.filter(pk=sigid).annotate(size=Count('crashentry'),quality=Min('crashentry__testcase__quality'))
    
    if not bucket:
        raise Http404
    
    bucket = bucket[0]
    
    entries = CrashEntry.objects.filter(bucket=sigid).filter(testcase__quality=bucket.quality).order_by('testcase__size', '-created')
    
    bucket.bestEntry = None
    if entries:
        bucket.bestEntry = entries[0]
    
    return render(request, 'signatures/view.html', { 'bucket' : bucket })

@login_required(login_url='/login/')
def editSignature(request, sigid):
    if request.method == 'POST':
        bucket = get_object_or_404(Bucket, pk=sigid)
        bucket.signature = request.POST['signature']
        bucket.shortDescription = request.POST['shortDescription']
        bucket.frequent = "frequent" in request.POST

        #TODO: FIXME: Update bug here as well
        return __handleSignaturePost(request, bucket)
    elif request.method == 'GET':
        if sigid != None:
            bucket = get_object_or_404(Bucket, pk=sigid)
            
            if 'fit' in request.GET:
                entry = get_object_or_404(CrashEntry, pk=request.GET['fit'])
                bucket.signature = bucket.getSignature().fit(entry.getCrashInfo())
            
            return render(request, 'signatures/edit.html', { 'bucket' : bucket })
        else:
            raise SuspiciousOperation
    else:
        raise SuspiciousOperation

@login_required(login_url='/login/')
def linkSignature(request, sigid):
    bucket = get_object_or_404(Bucket, pk=sigid)
    providers = BugProvider.objects.all() 
    
    data = { 'bucket' : bucket, 'providers' : providers }
    
    if request.method == 'POST':
        provider = get_object_or_404(BugProvider, pk=request.POST['provider'])
        bugId = request.POST['bugId']
        username = request.POST['username']
        password = request.POST['password']

        bug = Bug.objects.filter(externalId = bugId, externalType = provider)
        
        if 'submit_save' in request.POST:
            if not bug:
                bug = Bug(externalId = bugId, externalType = provider)
                bug.save()
            else:
                bug = bug[0]
                
            bucket.bug = bug
            bucket.save()
            return redirect('crashmanager:sigview', sigid=bucket.pk)
        else:
            # This is a preview request
            bugData = provider.getInstance().getBugData(bugId, username, password)
            
            if bugData == None:
                data['error_message'] = 'Bug not found in external database.'
            else:
                data['summary'] = bugData['summary']
            
            data['provider'] = provider
            data['bugId'] = bugId
            data['username'] = username
                
            return render(request, 'signatures/link.html', data)
    elif request.method == 'GET':
        return render(request, 'signatures/link.html', data)
    else:
        raise SuspiciousOperation

@login_required(login_url='/login/')
def unlinkSignature(request, sigid):
    bucket = get_object_or_404(Bucket, pk=sigid)
    
    if request.method == 'POST':
        bucket.bug = None
        bucket.save()        
        return redirect('crashmanager:sigview', sigid=bucket.pk)
    elif request.method == 'GET':
        return render(request, 'signatures/unlink.html', { 'bucket' : bucket })
    else:
        raise SuspiciousOperation
    
@login_required(login_url='/login/')
def trySignature(request, sigid, crashid):
    bucket = get_object_or_404(Bucket, pk=sigid)
    entry = get_object_or_404(CrashEntry, pk=crashid)
    
    signature = bucket.getSignature()
    entry.crashinfo = entry.getCrashInfo(attachTestcase=signature.matchRequiresTest())
    
    symptoms = signature.getSymptomsDiff(entry.crashinfo)
    
    return render(request, 'signatures/try.html', { 'bucket' : bucket, 'entry' : entry, 'symptoms' : symptoms })

@login_required(login_url='/login/')
def findSignatures(request, crashid):
    entry = get_object_or_404(CrashEntry, pk=crashid)
    
    entry.crashinfo = entry.getCrashInfo(attachTestcase=True)
    
    buckets = Bucket.objects.all()
    similarBuckets = []
    matchingBucket = None
    
    for bucket in buckets:
        signature = bucket.getSignature()
        distance = signature.getDistance(entry.crashinfo)
        
        # We found a matching bucket, no need to display/calculate similar buckets
        if distance == 0:
            matchingBucket = bucket
            break
            
        # TODO: This could be made configurable through a GET parameter
        if distance <= 4:
            proposedCrashSignature = signature.fit(entry.crashinfo)
            if proposedCrashSignature:
                # We now try to determine how this signature will behave in other buckets
                # If the signature matches lots of other buckets as well, it is likely too
                # broad and we should not consider it (or later rate it worse than others).
                matchesInOtherBuckets = 0
                nonMatchesInOtherBuckets = 0
                otherMatchingBucketIds = []
                for otherBucket in buckets:
                    if otherBucket.pk == bucket.pk:
                        continue
                    
                    bucketEntries = CrashEntry.objects.filter(bucket=otherBucket)
                    firstEntry = list(bucketEntries[:1])
                    if firstEntry:
                        firstEntry = firstEntry[0]
                        # Omit testcase for performance reasons for now
                        if proposedCrashSignature.matches(firstEntry.getCrashInfo(attachTestcase=False)):
                            matchesInOtherBuckets += 1
                            otherMatchingBucketIds.append(otherBucket.pk)
                        else:
                            nonMatchesInOtherBuckets += 1
                
                bucket.offCount = distance
                
                if matchesInOtherBuckets+nonMatchesInOtherBuckets > 0:
                    bucket.foreignMatchPercentage = round((float(matchesInOtherBuckets) / (matchesInOtherBuckets+nonMatchesInOtherBuckets)) * 100, 2)
                else:
                    bucket.foreignMatchPercentage = 0
                    
                bucket.foreignMatchCount = matchesInOtherBuckets
                
                if matchesInOtherBuckets == 0:
                    bucket.foreignColor = "green"
                elif matchesInOtherBuckets < 3:
                    bucket.foreignColor = "yellow"
                else:
                    bucket.foreignColor = "red"    
                
                # Set a limit to linking to the other matching buckets. It only makes sense to look at these
                # if the number is rather low and we would like to keep the URL short.
                bucket.linkToOthers = None
                if matchesInOtherBuckets <= 10:
                    bucket.linkToOthers = ",".join([str(x) for x in otherMatchingBucketIds])
                
                
                similarBuckets.append(bucket)
    
    if matchingBucket:
        entry.bucket = matchingBucket
        entry.save()
        return render(request, 'signatures/find.html', { 'bucket' : matchingBucket, 'crashentry' : entry })
    else:
        similarBuckets.sort(key=lambda x: x.offCount)
        return render(request, 'signatures/find.html', { 'buckets' : similarBuckets, 'crashentry' : entry })

@login_required(login_url='/login/')
def createExternalBug(request, crashid):
    entry = get_object_or_404(CrashEntry, pk=crashid)
    
    if not entry.bucket:
        return renderError(request, "Cannot create an external bug for an issue that is not associated to a bucket/signature")
    
    if request.method == 'POST':
        provider = get_object_or_404(BugProvider, pk=request.POST['provider'])
        
        # Let the provider handle the POST request, which will file the bug
        # and return us the external bug ID
        extBugId = provider.getInstance().handlePOSTCreate(request, entry)
        
        # Now create a bug in our database with that ID and assign it to the bucket
        extBug = Bug(externalId = extBugId, externalType = provider)
        extBug.save()
        entry.bucket.bug = extBug
        entry.bucket.save()
        
        return redirect('crashmanager:sigview', sigid=entry.bucket.pk)
    elif request.method == 'GET':
        if 'provider' in request.GET:
            provider = get_object_or_404(BugProvider, pk=request.GET['provider'])
        else:
            (user, created) = User.objects.get_or_create(user = request.user)
            provider = get_object_or_404(BugProvider, pk=user.defaultProviderId)
        
        return provider.getInstance().renderContextCreate(request, entry)
    else:
        raise SuspiciousOperation
    
@login_required(login_url='/login/')
def createBugTemplate(request, providerId):
    provider = get_object_or_404(BugProvider, pk=providerId)
    if request.method == 'POST':
        # Let the provider handle the template creation
        templateId = provider.getInstance().handlePOSTCreateEditTemplate(request)
        
        return redirect('crashmanager:viewtemplate', providerId=provider.pk, templateId=templateId)
    elif request.method == 'GET':
        return provider.getInstance().renderContextCreateTemplate(request)
    else:
        raise SuspiciousOperation
    
@login_required(login_url='/login/')
def viewEditBugTemplate(request, providerId, templateId):
    provider = get_object_or_404(BugProvider, pk=providerId)
    if request.method == 'GET':
        return provider.getInstance().renderContextViewTemplate(request, templateId)
    elif request.method == 'POST':
        templateId = provider.getInstance().handlePOSTCreateEditTemplate(request)
        return provider.getInstance().renderContextViewTemplate(request, templateId)



@login_required(login_url='/login/')
def viewBugProviders(request):
    providers = BugProvider.objects.annotate(size=Count('bug'))
    return render(request, 'providers/index.html', { 'providers' : providers })

@login_required(login_url='/login/')
def deleteBugProvider(request, providerId):
    provider = get_object_or_404(BugProvider, pk=providerId)
    if request.method == 'POST':
        # Deassociate all bugs
        bugs = Bug.objects.filter(externalType=provider.pk)
        buckets = Bucket.objects.filter(bug__in=bugs)
        for bucket in buckets:
            bucket.bug = None
            bucket.save()
        
        provider.delete()
        return redirect('crashmanager:bugproviders')
    elif request.method == 'GET':
        return render(request, 'providers/remove.html', { 'provider' : provider })
    else:
        raise SuspiciousOperation

@login_required(login_url='/login/')
def viewBugProvider(request, providerId):
    provider = BugProvider.objects.filter(pk=providerId).annotate(size=Count('bug'))
    
    if not provider:
        raise Http404
    
    provider = provider[0]
    
    return render(request, 'providers/view.html', { 'provider' : provider })

@login_required(login_url='/login/')
def editBugProvider(request, providerId):
    provider = get_object_or_404(BugProvider, pk=providerId)
    if request.method == 'POST':
        provider.classname = request.POST['classname']
        provider.hostname = request.POST['hostname']
        provider.urlTemplate = request.POST['urlTemplate']
        
        try:
            provider.getInstance()
        except Exception as e:
            return render(request, 'providers/edit.html', { 'provider' : provider, 'error_message' : e })
        
        provider.save()
        return redirect('crashmanager:bugproviders')
    elif request.method == 'GET':
        return render(request, 'providers/edit.html', { 'provider' : provider })
    else:
        raise SuspiciousOperation
    
@login_required(login_url='/login/')
def createBugProvider(request):
    if request.method == 'POST':
        provider = BugProvider(classname=request.POST['classname'], hostname=request.POST['hostname'], urlTemplate=request.POST['urlTemplate'])
        
        try:
            provider.getInstance()
        except Exception as e:
            return render(request, 'providers/edit.html', { 'provider' : provider, 'error_message' : e })
        
        provider.save()
        return redirect('crashmanager:bugproviders')
    elif request.method == 'GET':
        return render(request, 'providers/edit.html', {})
    else:
        raise SuspiciousOperation
    
@login_required(login_url='/login/')
def userSettings(request):
    (user, created) = User.objects.get_or_create(user = request.user)
    
    def createUserSettingsData(user, msg = None):
        tools = Tool.objects.all()
        currentToolsFilter = user.defaultToolsFilter.all()
            
        for tool in tools:
            tool.checked = tool in currentToolsFilter
        
        providers = BugProvider.objects.all()
        provider = providers.filter(pk = user.defaultProviderId)
        templates = None
        if provider:
            provider = provider[0]
            templates = provider.getInstance().getTemplateList()
            
        return { 
                "user" : user, 
                "tools" : tools,
                "providers" : providers,
                "templates" : templates,
                "defaultProviderId" : user.defaultProviderId,
                "defaultTemplateId" : user.defaultTemplateId,
                "msg" : msg,
                }

    if request.method == 'POST':     
        if "changefilter" in request.POST:
            user.defaultToolsFilter.clear()
            user.defaultToolsFilter = [Tool.objects.get(name=x.replace("tool_", "", 1)) for x in request.POST if x.startswith("tool_")]
            data = createUserSettingsData(user, msg = "Tools filter updated successfully.")
        elif "changetemplate" in request.POST:
            user.defaultProviderId = int(request.POST['defaultProvider'])
            user.defaultTemplateId = int(request.POST['defaultTemplate'])
            user.save()
            data = createUserSettingsData(user, msg = "Default provider/template updated successfully.")
        else:
            raise SuspiciousOperation
        
        return render(request, 'usersettings.html', data)
    elif request.method == 'GET':
        return render(request, 'usersettings.html', createUserSettingsData(user))
    else:
        raise SuspiciousOperation

class CrashEntryViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows adding/viewing CrashEntries
    """
    authentication_classes = (TokenAuthentication,)
    queryset = CrashEntry.objects.all()
    serializer_class = CrashEntrySerializer

class BucketViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint that allows reading of signatures
    """
    authentication_classes = (TokenAuthentication,)
    queryset = Bucket.objects.all()
    serializer_class = BucketSerializer
