def candidate_payload(name='Ника',email='nika@example.org',password='test-password-123',
                      specialization='Backend',grade='Junior'):
    return {
        'name':name,'email':email,'password':password,'role':'candidate',
        'profile':{'version':1,'name':name,'email':email,'fsp':'','salaryMin':70000,'terms':True,'step':4,
                   'selected':[specialization],'active':specialization,
                   'stacks':{specialization:[]},'jobs':{},'ownProjects':{}},
        'specialization_grades':{specialization:grade},
    }
